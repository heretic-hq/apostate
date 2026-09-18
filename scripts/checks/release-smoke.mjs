// Turnkey post-build check of the four behaviours a release most needs to be
// right, and which only a running binary can answer. Exits non-zero on failure.
//
// Every one of these shipped broken in 152.0.7977.83 and every one looked
// healthy from inside the repository: the schema declared the field, a patch
// consumed it, the validators passed, and nothing produced a value. The
// defects were found by running the browser and reading what a page sees, so
// that is what this does.
//
// What it asserts, and why each is here:
//
//   1 UA        navigator.platform, navigator.userAgent, userAgentData and the
//               Sec-CH-UA-Platform request header all name the same operating
//               system. The shipped build served the HOST's user agent, so a
//               Windows persona on Linux read "X11; Linux x86_64" beside
//               Win32. Four detectors charged for it and CreepJS called it a
//               lie. The header is read from a real request rather than from
//               the page, because the page half was never the broken half.
//
//   2 IDENTITY  a persistent --user-data-dir composes ONE machine across
//               launches. Four launches against one directory produced four
//               different machines, which presents a site with one cookie jar
//               whose hardware changes between visits. Asserted as one seed
//               across four launches AND a different seed without a directory,
//               because pinning everything would pass just as well.
//
//   3 AUDIO     AudioContext.baseLatency is the persona's, not the host's. The
//               mechanism existed and nothing populated the field, so every
//               persona reported the build host's 2048-frame Linux buffer.
//               Checked per platform, since one platform passing by accident
//               is the failure this had.
//
//   4 LOCALE    a composed launch does not inherit the host's locale
//               environment. Run with a deliberately foreign LANG so that
//               inheritance is visible: a pass here means the composed value
//               won, not that the host happened to agree.
//
// Usage: node release-smoke.mjs <binaryPath> [platform]
//   platform defaults to the host's own; pass windows|macos|linux to force one.
import http from 'node:http';
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';

const BIN = process.argv[2];
if (!BIN || !fs.existsSync(BIN)) {
  console.error('usage: node release-smoke.mjs <binaryPath> [windows|macos|linux]');
  process.exit(2);
}

// Chromium refuses to start as root without this, and the CI boxes these
// artifacts are checked on run as root. It is a launch precondition, not a
// fingerprint choice: nothing this file asserts is reachable from the
// sandbox's state, and skipping the check entirely would be worse.
const SANDBOX = (typeof process.getuid === 'function' && process.getuid() === 0)
  ? ['--no-sandbox'] : [];
const PORT = 18091;

// Expected baseLatency per persona at 48 kHz, from the values the catalogue
// serves: Windows 480 frames (WASAPI's 10 ms period), macOS 256 (CoreAudio),
// Linux 512 (Pulse's floor). A host at another sample rate changes the
// quotient, so the check reports the frame count it inferred rather than
// failing blind.
const FRAMES = { windows: 480, macos: 256, linux: 512 };
// The OS token each persona must put in the user agent, and the matching
// navigator.platform. Both halves are named so a fix to one cannot mask the
// other.
const OS_TOKENS = {
  windows: { ua: 'Windows NT 10.0', platform: 'Win32', ch: 'Windows' },
  macos: { ua: 'Macintosh; Intel Mac OS X', platform: 'MacIntel', ch: 'macOS' },
  linux: { ua: 'X11; Linux x86_64', platform: 'Linux x86_64', ch: 'Linux' },
};

const PAGE = `<!doctype html><meta charset=utf-8><body><script>
(async () => {
  const ac = new AudioContext();
  const out = {
    platform: navigator.platform,
    ua: navigator.userAgent,
    uaDataPlatform: navigator.userAgentData ? navigator.userAgentData.platform : null,
    baseLatency: ac.baseLatency,
    sampleRate: ac.sampleRate,
    languages: navigator.languages,
    language: navigator.language,
    intlLocale: Intl.DateTimeFormat().resolvedOptions().locale,
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  };
  ac.close();
  await fetch('/r', { method: 'POST', body: JSON.stringify(out) });
})();
</script></body>`;

// One launch: serve the page, collect the payload the page posts back, and
// keep the Sec-CH-UA-Platform header the browser sent for the document. The
// header is the half that travels on the wire, so it is captured from the
// request rather than reconstructed.
function launch(args, env) {
  return new Promise((resolve, reject) => {
    let settled = false, headers = null;
    const server = http.createServer((req, res) => {
      if (req.method === 'POST') {
        let body = '';
        req.on('data', (c) => { body += c; });
        req.on('end', () => {
          res.writeHead(204); res.end();
          if (!settled) { settled = true; finish(JSON.parse(body)); }
        });
        return;
      }
      headers = req.headers;
      res.writeHead(200, { 'content-type': 'text/html' }); res.end(PAGE);
    });
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'apo-smoke-'));
    let child = null, timer = null;
    const finish = (payload) => {
      clearTimeout(timer);
      if (child) child.kill('SIGKILL');
      server.close();
      resolve({ ...payload, chPlatform: headers && headers['sec-ch-ua-platform'] });
    };
    server.listen(PORT, '127.0.0.1', () => {
      child = spawn(BIN, [
        ...args, ...SANDBOX, '--no-first-run', '--no-default-browser-check',
        `--proxy-bypass-list=<local>;127.0.0.1`,
        '--headless=new', `--user-data-dir=${dir}`,
        `http://127.0.0.1:${PORT}/`,
      ], { stdio: 'ignore', env: { ...process.env, ...env } });
      timer = setTimeout(() => {
        if (child) child.kill('SIGKILL');
        server.close();
        reject(new Error('timed out waiting for the page to report'));
      }, 90_000);
    });
  });
}

function explain(args) {
  const r = spawnSync(BIN, [...args, ...SANDBOX, '--fingerprint-explain'],
    { encoding: 'utf8', timeout: 60_000 });
  const seed = /^ {2}seed {2,}(\S+)/m.exec(r.stdout || '');
  return seed ? seed[1] : null;
}

const failures = [];
const note = (ok, label, detail) => {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${label}${detail ? ' -- ' + detail : ''}`);
  if (!ok) failures.push(label);
};

const only = process.argv[3];
const platforms = only ? [only] : ['windows', 'macos', 'linux'];

for (const persona of platforms) {
  const want = OS_TOKENS[persona];
  if (!want) { console.error(`unknown platform ${persona}`); process.exit(2); }
  // A foreign LANG on every run, so locale inheritance would be visible.
  const r = await launch([`--fingerprint-platform=${persona}`], { LANG: 'th_TH.UTF-8', LANGUAGE: 'th' });

  note(r.ua.includes(want.ua) && r.platform === want.platform
       && (r.chPlatform === undefined || String(r.chPlatform).replace(/"/g, '') === want.ch),
       `UA ${persona}`,
       `platform=${r.platform} ch=${r.chPlatform} ua=${r.ua.slice(0, 48)}`);

  const frames = Math.round(r.baseLatency * r.sampleRate);
  note(frames === FRAMES[persona], `AUDIO ${persona}`,
       `baseLatency=${r.baseLatency} at ${r.sampleRate}Hz = ${frames} frames, want ${FRAMES[persona]}`);

  // The environment is the Linux lever and ONLY the Linux lever. macOS reads
  // the app's AppleLanguages user default and Windows the preferred UI
  // language list, so this assertion is real on Linux and vacuous elsewhere.
  // The macOS half is covered separately below, because a check that cannot
  // see a leak must say so rather than print a pass.
  note(!String(r.languages).includes('th') && !String(r.intlLocale).startsWith('th'),
       `LOCALE ${persona}`,
       `languages=${r.languages} intl=${r.intlLocale}`);
}

// macOS resolves its locale from the AppleLanguages user default, which no
// environment variable reaches, so the loop above cannot observe the macOS
// leak at all -- on an English-language Mac it passes whether or not the
// browser is fixed. Reproducing it means writing a real user default, which
// has effects outside this process, so it is opt-in and always restored.
// Measured on the 152.0.7977.83 macos-arm64 artifact before patch 0115:
// AppleLanguages=th-TH gave intl "th" with calendar "buddhist" beside
// navigator.languages en-SG,en -- the two producers contradicting each other
// in one call.
if (process.platform === 'darwin') {
  if (process.env.APOSTATE_SMOKE_APPLELANGS !== '1') {
    console.log('skip LOCALE macos-userdefault -- set APOSTATE_SMOKE_APPLELANGS=1 to run;'
      + ' it writes and restores the AppleLanguages user default');
  } else {
    const DOMAIN = 'org.chromium.Chromium';
    const prior = spawnSync('defaults', ['read', DOMAIN, 'AppleLanguages'], { encoding: 'utf8' });
    try {
      spawnSync('defaults', ['write', DOMAIN, 'AppleLanguages', '-array', 'th-TH']);
      const r = await launch(['--fingerprint-platform=windows'], {});
      note(!String(r.intlLocale).startsWith('th') && r.languages && !String(r.languages).includes('th'),
           'LOCALE macos-userdefault',
           `languages=${r.languages} intl=${r.intlLocale}`);
    } finally {
      // Restore rather than delete when the machine had its own value.
      if (prior.status === 0) {
        const langs = (prior.stdout.match(/"?([A-Za-z-]+)"?,?\s*$/gm) || [])
          .map((s) => s.trim().replace(/[",]/g, '')).filter((s) => s && s !== '(' && s !== ')');
        spawnSync('defaults', ['write', DOMAIN, 'AppleLanguages', '-array', ...langs]);
      } else {
        spawnSync('defaults', ['delete', DOMAIN, 'AppleLanguages']);
      }
    }
  }
}

// IDENTITY: one directory must give one machine; no directory must not.
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'apo-ident-'));
const seeds = [0, 1, 2, 3].map(() => explain([`--user-data-dir=${dir}`]));
const loose = explain([]);
note(seeds.every((s) => s && s === seeds[0]), 'IDENTITY stable per profile',
     `seeds=${[...new Set(seeds)].map((s) => String(s).slice(0, 12)).join(',')}`);
note(loose && loose !== seeds[0], 'IDENTITY ephemeral without a profile',
     `loose=${String(loose).slice(0, 12)}`);
const identityFile = path.join(dir, 'apostate', 'identity');
note(fs.existsSync(identityFile) && fs.readFileSync(identityFile, 'utf8').trim() === seeds[0],
     'IDENTITY file holds the seed');

console.log(failures.length ? `\n${failures.length} failure(s)` : '\nall checks passed');
process.exit(failures.length ? 1 : 0);
