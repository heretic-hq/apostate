#!/usr/bin/env node
// Compare two browsers on one host, surface by surface.
//
// This exists because the only useful question about a stealth change is
// "compared to what, on the same machine". Every figure this project got
// wrong today was wrong because it had no control: a claimed maximum that
// looked servable until something asked whether it was usable, a served
// value that looked like the target until the host turned out to report the
// same number, an extension list that looked like a superset until it was
// actually differenced. So this runs one probe against two binaries back to
// back and prints the difference, rather than describing either alone.
//
// Usage:
//   node scripts/checks/compare-against-reference.mjs \
//     --a /path/to/apostate/chrome --b /usr/bin/google-chrome [--args-a ...]
//
// --a and --b are launched with identical flags unless --args-a / --args-b
// add to them. Anything a wrapper has to start instead (a licensed binary, a
// Python launcher) is out of scope: run it yourself against --serve and pass
// the URL, which is what --serve-only is for.

import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));

function arg(name, fallback = null) {
  const i = process.argv.indexOf(`--${name}`);
  return i === -1 ? fallback : process.argv[i + 1];
}
const flag = (name) => process.argv.includes(`--${name}`);

// The probe is a separate file so it can be read, reviewed and reused by the
// capture collector without being embedded in a string literal here.
const PROBE = await readFile(join(HERE, "surface-probe.html"), "utf8");

async function serve() {
  const results = [];
  const server = createServer((req, res) => {
    if (req.method === "POST") {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        try {
          results.push(JSON.parse(body));
        } catch (err) {
          results.push({ error: String(err), raw: body.slice(0, 400) });
        }
        res.writeHead(204).end();
      });
      return;
    }
    res.writeHead(200, { "content-type": "text/html" }).end(PROBE);
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  return { server, results, url: `http://127.0.0.1:${server.address().port}/` };
}

async function run(binary, url, extra) {
  const profile = await mkdtemp(join(tmpdir(), "apostate-cmp-"));
  const args = [
    "--headless=new",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    `--user-data-dir=${profile}`,
    ...extra,
    url,
  ];
  const child = spawn(binary, args, { stdio: "ignore" });
  // The probe POSTs when it is done; the deadline is a backstop, not the
  // mechanism, because a browser that never reaches the page must be a
  // reported failure rather than an empty result that reads as agreement.
  const exited = new Promise((resolve) => child.once("exit", resolve));
  await Promise.race([exited, new Promise((r) => setTimeout(r, 45_000))]);
  child.kill("SIGKILL");
  await rm(profile, { recursive: true, force: true });
}

function flatten(value, prefix = "", out = {}) {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    for (const [k, v] of Object.entries(value)) {
      flatten(v, prefix ? `${prefix}.${k}` : k, out);
    }
  } else {
    out[prefix] = JSON.stringify(value);
  }
  return out;
}

const { server, results, url } = await serve();

if (flag("serve-only")) {
  console.log(url);
  console.log("serving the probe; POST results arrive here. Ctrl-C to stop.");
  await new Promise(() => {});
}

const a = arg("a");
const b = arg("b");
if (!a || !b) {
  console.error("usage: --a <binary> --b <binary> [--args-a '…'] [--args-b '…']");
  console.error("       --serve-only          print a URL and wait, for wrapper-launched browsers");
  server.close();
  process.exit(2);
}

const extraA = (arg("args-a", "") || "").split(" ").filter(Boolean);
const extraB = (arg("args-b", "") || "").split(" ").filter(Boolean);

await run(a, url, extraA);
const gotA = results.length;
await run(b, url, extraB);
server.close();

if (results.length < 2) {
  console.error(
    `only ${results.length} of 2 browsers reported. ` +
      `A missing result is a failed launch, not agreement — check the binary path and flags.`,
  );
  process.exit(1);
}

const [A, B] = results;
const fa = flatten(A);
const fb = flatten(B);
const keys = [...new Set([...Object.keys(fa), ...Object.keys(fb)])].sort();

const same = [];
const diff = [];
for (const k of keys) {
  (fa[k] === fb[k] ? same : diff).push(k);
}

const width = Math.max(...keys.map((k) => k.length), 10);
console.log(`A = ${a} ${extraA.join(" ")}`);
console.log(`B = ${b} ${extraB.join(" ")}`);
console.log("");
console.log(`${"surface".padEnd(width)} | ${"A".padEnd(38)} | B`);
console.log("-".repeat(width + 3 + 38 + 3 + 38));
for (const k of diff) {
  const va = (fa[k] ?? "<absent>").slice(0, 38);
  const vb = (fb[k] ?? "<absent>").slice(0, 38);
  console.log(`${k.padEnd(width)} | ${va.padEnd(38)} | ${vb}`);
}
console.log("");
console.log(`${diff.length} surface(s) differ, ${same.length} identical`);
// Identical surfaces are printed by name because "the two agree here" is the
// result that stops the next person re-measuring, and a count alone does not
// carry it.
if (same.length) console.log(`identical: ${same.join(", ")}`);
