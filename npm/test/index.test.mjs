import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer, request as httpRequest } from "node:http";
import { chmod, readFile, writeFile } from "node:fs/promises";
import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import test from "node:test";
import {
  BinaryExtractionError,
  CATALOGUE_VERSION,
  CHROMIUM_VERSION,
  ProfileResolutionError,
  UnpublishedArtifactError,
  ensureBinary,
  launch,
  loadCatalogue,
  resolveProfile,
  toCanonicalLaunchConfig,
} from "../dist/index.js";

const target = "linux-x64";
const artifactNameFor = (platform) => `apostate-${CHROMIUM_VERSION}-${platform}.${platform === "windows-x64" ? "zip" : "tar.zst"}`;
function releaseManifest(archive, platform = target) {
  const artifact = artifactNameFor(platform);
  return {
    package_version: "0.1.0",
    chromium_version: CHROMIUM_VERSION,
    catalogue_version: CATALOGUE_VERSION,
    platform,
    artifact,
    sha256: createHash("sha256").update(archive).digest("hex"),
    url: `https://example.invalid/${artifact}`,
  };
}

function crc32(value) {
  let crc = 0xffffffff;
  for (const byte of value) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function storedZip(entries) {
  const localParts = [];
  const centralParts = [];
  let offset = 0;
  for (const entry of entries) {
    const name = Buffer.from(entry.name, "utf8");
    const data = Buffer.from(entry.data ?? "", "utf8");
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt32LE(crc32(data), 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(name.length, 26);
    localParts.push(Buffer.concat([local, name, data]));
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(entry.mode ? (3 << 8) | 20 : 20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt32LE(crc32(data), 16);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(name.length, 28);
    if (entry.mode) central.writeUInt32LE((entry.mode << 16) >>> 0, 38);
    centralParts.push(Buffer.concat([central, name]));
    offset += local.length + name.length + data.length;
  }
  const centralDirectory = Buffer.concat(centralParts);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(centralDirectory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...localParts, centralDirectory, end]);
}

function tarArchive(entries) {
  const chunks = [];
  const writeField = (header, value, start, length) => {
    const encoded = Buffer.from(String(value), "ascii");
    encoded.copy(header, start, 0, Math.min(encoded.length, length - 1));
  };
  for (const entry of entries) {
    const header = Buffer.alloc(512);
    writeField(header, entry.name, 0, 100);
    writeField(header, "0000777", 100, 8);
    writeField(header, "0000000", 108, 8);
    writeField(header, "0000000", 116, 8);
    const data = entry.type === "2" ? Buffer.alloc(0) : Buffer.from(entry.data ?? "", "utf8");
    writeField(header, data.length.toString(8).padStart(11, "0"), 124, 12);
    writeField(header, "00000000000", 136, 12);
    header.fill(0x20, 148, 156);
    header.write(entry.type ?? "0", 156, 1, "ascii");
    if (entry.linkname) writeField(header, entry.linkname, 157, 100);
    header.write("ustar\0", 257, 6, "ascii");
    writeField(header, "00", 263, 8);
    const checksum = [...header].reduce((sum, byte) => sum + byte, 0);
    writeField(header, checksum.toString(8).padStart(6, "0"), 148, 8);
    chunks.push(header, data);
    if (data.length % 512) chunks.push(Buffer.alloc(512 - (data.length % 512)));
  }
  chunks.push(Buffer.alloc(1024));
  return Buffer.concat(chunks);
}


const shippedCataloguePath = new URL("../assets/catalogue.json", import.meta.url);

async function catalogueFixture(root, mutate) {
  const catalogue = JSON.parse(await readFile(shippedCataloguePath, "utf8"));
  mutate(catalogue);
  const cataloguePath = join(root, "catalogue.json");
  await writeFile(cataloguePath, JSON.stringify(catalogue));
  return cataloguePath;
}


test("translates canonical launch fields and accepts stable string seeds", () => {
  const config = toCanonicalLaunchConfig({
    fingerprint: "seed:stable-01",
    fingerprintPlatform: "macos",
    userDataDir: "/tmp/apostate-profile",
    headless: false,
    args: ["--disable-gpu"],
    proxy: { server: "http://proxy.example:8080", username: "user", password: "secret" },
  });
  assert.equal(config.fingerprint, "seed:stable-01");
  assert.equal(config.fingerprint_platform, "macos");
  assert.equal(config.user_data_dir, "/tmp/apostate-profile");
  assert.equal(config.headless, false);
  assert.deepEqual(config.args, ["--disable-gpu"]);
  assert.match(config.proxy, /^http:\/\/user:secret@proxy\.example:8080\/$/);
  assert.throws(() => toCanonicalLaunchConfig({ fingerprint: "bad seed" }), /fingerprint/);
});
test("rejects humanize until native behavior exists", () => {
  assert.throws(
    () => toCanonicalLaunchConfig({ humanize: true }),
    /humanize is not implemented/,
  );
});
test("loads the version 2 catalogue and reports its anchors, axes and policy ids", () => {
  const catalogue = loadCatalogue();
  assert.deepEqual(Object.keys(catalogue).sort(), [
    "anchors",
    "axes",
    "browser_build",
    "catalogue_version",
    "model",
    "policies",
    "profile_schema_version",
  ]);
  assert.equal(catalogue.catalogue_version, 2);
  assert.equal(catalogue.profile_schema_version, 3);
  assert.equal(catalogue.browser_build, CHROMIUM_VERSION);
  assert.equal(catalogue.model, "anchors+dispersion");
  assert.ok(catalogue.anchors.length > 0);
  for (const anchor of catalogue.anchors) {
    assert.deepEqual(Object.keys(anchor).sort(), ["backend", "id", "members", "platform", "rotation_status"]);
    assert.ok(["linux", "macos", "windows"].includes(anchor.platform));
    assert.ok(anchor.members.length > 0);
    assert.ok(anchor.members.every((member) => typeof member === "string" && member.length > 0));
  }
  assert.ok(catalogue.axes.length > 0);
  for (const axis of catalogue.axes) {
    assert.deepEqual(Object.keys(axis).sort(), ["axis", "conditioned_on", "option_sets", "options", "selection", "servability"]);
  }
  assert.equal(new Set(catalogue.axes.map((axis) => axis.axis)).size, catalogue.axes.length);
  assert.ok(catalogue.policies.locale.length > 0);
  assert.ok(catalogue.policies.theme.length > 0);
});

test("rejects a catalogue that disagrees with the package or still carries the retired model", async () => {
  const root = await mkdtemp(join(tmpdir(), "apostate-node-catalogue-v2-"));
  try {
    assert.equal(loadCatalogue(await catalogueFixture(root, () => {})).model, "anchors+dispersion");
    for (const mutate of [
      (catalogue) => { catalogue.catalogue_version = 1; },
      (catalogue) => { catalogue.profile_schema_version = 2; },
      (catalogue) => { catalogue.browser_build = "1.2.3.4"; },
      (catalogue) => { catalogue.model = "fixed-catalogue"; },
      (catalogue) => { delete catalogue.catalogue_id; },
      (catalogue) => { delete catalogue.anchors; },
      (catalogue) => { delete catalogue.axes; },
      (catalogue) => { catalogue.anchors[0].members = []; },
      (catalogue) => { catalogue.anchors[0].rotation_status = ""; },
      (catalogue) => { catalogue.axes[0].conditioned_on = "platform"; },
      (catalogue) => { catalogue.axes[0].options = 0; },
      (catalogue) => { catalogue.policies.gpu = [{ id: "unexpected" }]; },
      (catalogue) => { catalogue.families = [{ id: "retired" }]; },
      (catalogue) => { catalogue.family_count = 14; },
    ]) {
      const cataloguePath = await catalogueFixture(root, mutate);
      assert.throws(() => loadCatalogue(cataloguePath), ProfileResolutionError);
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("fails closed instead of composing a profile or resolving a catalogue id", () => {
  for (const options of [
    {},
    { fingerprint: 12345 },
    { fingerprintPlatform: "windows" },
    { fingerprint: "seed:stable-01", fingerprintPlatform: "macos" },
    { fingerprint: "host", fingerprintPlatform: "windows" },
  ]) {
    assert.throws(() => resolveProfile(options), (error) => {
      assert.ok(error instanceof ProfileResolutionError);
      assert.equal(error.code, "APOSTATE_COMPOSITION_UNAVAILABLE");
      return true;
    });
  }
  assert.throws(() => resolveProfile({ fingerprint: 12345 }), /in-binary compositor is not yet wired to this package/);
  assert.throws(() => resolveProfile({ profileId: "retired-id" }), (error) => {
    assert.ok(error instanceof ProfileResolutionError);
    assert.equal(error.code, "APOSTATE_CATALOGUE_PROFILE_IDS_RETIRED");
    assert.match(error.message, /composes a profile from anchors and dispersion/);
    return true;
  });
  const host = resolveProfile({ fingerprint: "host" });
  assert.equal(host.profile, null);
  assert.equal(host.source, "host-inherited");
  assert.equal(host.profileId, null);
});

test("rejects explicit profile and profile-file platform mismatches", async () => {
  const root = await mkdtemp(join(tmpdir(), "apostate-node-profile-platform-"));
  const profile = { id: "explicit", platform: { name: "Windows" } };
  try {
    assert.throws(
      () => resolveProfile({ profile, fingerprintPlatform: "macos" }),
      /does not match fingerprint platform/,
    );
    const profilePath = join(root, "explicit.json");
    await writeFile(profilePath, JSON.stringify(profile));
    assert.throws(
      () => resolveProfile({ profilePath, fingerprintPlatform: "macos" }),
      /does not match fingerprint platform/,
    );
    const matching = resolveProfile({ profile, fingerprintPlatform: "win32" });
    assert.equal(matching.profileId, "explicit");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("strips source_capture and applies timezone and WebRTC proxy policy", async () => {
  const root = await mkdtemp(join(tmpdir(), "apostate-node-source-capture-"));
  const argvPath = join(root, "argv.json");
  const executable = join(root, "capture-argv.mjs");
  const profile = { id: "explicit", source_capture: "capture-2026-09-13", platform: { name: "macOS" } };
  try {
    await writeFile(executable, "#!/usr/bin/env node\nimport { writeFileSync } from \"node:fs\";\nwriteFileSync(process.env.APOSTATE_ARGV_PATH, JSON.stringify({ argv: process.argv.slice(2), timezone: process.env.TZ }));\nsetTimeout(() => {}, 10000);\n");
    await chmod(executable, 0o755);
    const browser = await launch({
      executablePath: executable,
      profile,
      fingerprintPlatform: "macos",
      timezone: "Asia/Karachi",
      proxy: "http://proxy.example:8080",
      args: ["--fingerprint-webrtc-ip=198.51.100.7"],
      geoip: false,
      headless: false,
      env: { APOSTATE_ARGV_PATH: argvPath },
    });
    try {
      let launchData;
      for (let attempt = 0; attempt < 40; attempt += 1) {
        try {
          launchData = JSON.parse(await readFile(argvPath, "utf8"));
          break;
        } catch (error) {
          if (attempt === 39) throw error;
          await new Promise((resolve) => setTimeout(resolve, 25));
        }
      }
      const argv = launchData.argv;
      const encoded = argv.find((value) => value.startsWith("--apostate-profile="));
      assert.ok(encoded);
      const payload = JSON.parse(Buffer.from(encoded.slice("--apostate-profile=".length), "base64").toString("utf8"));
      assert.equal(payload.device_profile, undefined);
      assert.equal(payload.id, "explicit");
      assert.equal(payload.source_capture, undefined);
      assert.equal(payload.locale.timezone, "Asia/Karachi");
      assert.equal(launchData.timezone, "Asia/Karachi");
      assert.equal(argv.includes("--fingerprint-webrtc-ip=198.51.100.7"), true);
      assert.equal(argv.includes("--force-webrtc-ip-handling-policy=disable_non_proxied_udp"), true);
      assert.equal(browser.launchConfig.profile.source_capture, profile.source_capture);
    } finally {
      await browser.close();
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("geoip resolves through an authenticated HTTP proxy without leaking credentials to argv", async () => {
  const root = await mkdtemp(join(tmpdir(), "apostate-node-geoip-proxy-"));
  const argvPath = join(root, "argv.json");
  const executable = join(root, "capture-argv.mjs");
  const targetServer = createServer((request, response) => {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ ip: "202.63.209.57", languages: "en-US,en", timezone: "UTC" }));
  });
  let sawProxyAuth = false;
  const proxyServer = createServer((request, response) => {
    sawProxyAuth = request.headers["proxy-authorization"] === `Basic ${Buffer.from("fixture-user:fixture-password").toString("base64")}`;
    const upstream = httpRequest(request.url, { headers: { accept: request.headers.accept ?? "" } }, (upstreamResponse) => {
      response.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers);
      upstreamResponse.pipe(response);
    });
    upstream.on("error", () => response.writeHead(502).end());
    upstream.end();
  });
  try {
    await new Promise((resolve) => targetServer.listen(0, "127.0.0.1", resolve));
    await new Promise((resolve) => proxyServer.listen(0, "127.0.0.1", resolve));
    await writeFile(executable, "#!/usr/bin/env node\nimport { writeFileSync } from \"node:fs\";\nwriteFileSync(process.env.APOSTATE_ARGV_PATH, JSON.stringify(process.argv.slice(2)));\nsetTimeout(() => {}, 10000);\n");
    await chmod(executable, 0o755);
    const targetUrl = `http://127.0.0.1:${targetServer.address().port}/geoip`;
    const proxyUrl = `http://127.0.0.1:${proxyServer.address().port}`;
    const browser = await launch({
      executablePath: executable,
      profile: { id: "geoip-fixture", platform: { name: "macOS" } },
      fingerprintPlatform: "macos",
      proxy: { server: proxyUrl, username: "fixture-user", password: "fixture-password" },
      geoipUrl: targetUrl,
      geoip: true,
      env: { APOSTATE_ARGV_PATH: argvPath },
    });
    let argv;
    for (let attempt = 0; attempt < 40; attempt += 1) {
      try {
        argv = JSON.parse(await readFile(argvPath, "utf8"));
        break;
      } catch (error) {
        if (attempt === 39) throw error;
        await new Promise((resolve) => setTimeout(resolve, 25));
      }
    }
    try {
      assert.equal(browser.launchConfig.locale, "en-US");
      assert.equal(browser.launchConfig.timezone, "UTC");
      assert.equal(argv.includes("--fingerprint-webrtc-ip=202.63.209.57"), true);
      assert.equal(sawProxyAuth, true);
      assert.equal(argv.some((value) => value.includes("fixture-user") || value.includes("fixture-password")), false);
      assert.equal(argv.includes(`--proxy-server=${proxyUrl}`), true);
      const encoded = argv.find((value) => value.startsWith("--apostate-profile="));
      assert.ok(encoded);
      const payload = JSON.parse(Buffer.from(encoded.slice("--apostate-profile=".length), "base64").toString("utf8"));
      assert.equal(payload.device_profile.id, "geoip-fixture");
      assert.deepEqual(payload.proxy_credentials, { username: "fixture-user", password: "fixture-password" });
      assert.equal(payload.id, undefined);
    } finally {
      await browser.close();
    }
  } finally {
    await new Promise((resolve) => proxyServer.close(resolve));
    await new Promise((resolve) => targetServer.close(resolve));
    await rm(root, { recursive: true, force: true });
  }
});

test("rejects unpublished manifests before downloading or extracting", async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-unpublished-"));
  let downloads = 0;
  let extracts = 0;
  try {
    await assert.rejects(
      ensureBinary({
        target,
        cacheDir,
        manifest: {
          package_version: "0.1.0",
          chromium_version: CHROMIUM_VERSION,
          catalogue_version: CATALOGUE_VERSION,
          artifacts: {},
          status: "unpublished",
        },
        download: async () => {
          downloads += 1;
          return Buffer.from("archive");
        },
        extract: async () => {
          extracts += 1;
          return null;
        },
      }),
      (error) => error instanceof UnpublishedArtifactError && /unpublished/i.test(error.message),
    );
    assert.equal(downloads, 0);
    assert.equal(extracts, 0);
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test("rejects an unpublished platform without downloading another platform's artifact", async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-subset-"));
  const records = ["linux-x64", "linux-arm64", "macos-arm64"].map((platform) => ({
    platform,
    artifact: artifactNameFor(platform),
    sha256: "0".repeat(64),
  }));
  let downloads = 0;
  try {
    for (const manifest of [
      { package_version: "0.1.0", chromium_version: CHROMIUM_VERSION, catalogue_version: CATALOGUE_VERSION, artifacts: Object.fromEntries(records.map((record) => [record.platform, record])) },
      { package_version: "0.1.0", chromium_version: CHROMIUM_VERSION, catalogue_version: CATALOGUE_VERSION, artifacts: records },
      { package_version: "0.1.0", chromium_version: CHROMIUM_VERSION, catalogue_version: CATALOGUE_VERSION, ...records[0] },
    ]) {
      await assert.rejects(
        ensureBinary({
          target: "windows-x64",
          cacheDir,
          manifest,
          download: async () => { downloads += 1; throw new Error("unexpected download"); },
        }),
        (error) => error instanceof UnpublishedArtifactError && /windows-x64.*not published for this release/.test(error.message),
      );
    }
    assert.equal(downloads, 0);
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test("accepts scalar release manifest and rejects tampered cache state", async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-cache-"));
  try {
    const archive = Buffer.from("archive bytes");
    const manifest = releaseManifest(archive);
    let downloads = 0;
    let extracts = 0;
    const options = {
      target,
      cacheDir,
      manifest,
      download: async () => {
        downloads += 1;
        return archive;
      },
      extract: async (_bytes, destination) => {
        extracts += 1;
        const path = join(destination, "apostate");
        await writeFile(path, "binary bytes");
        return path;
      },
    };
    const binary = await ensureBinary(options);
    assert.equal(downloads, 1);
    assert.equal(extracts, 1);
    assert.equal(await readFile(binary, "utf8"), "binary bytes");

    await ensureBinary(options);
    assert.equal(downloads, 1, "verified cache should avoid a second download");
    assert.equal(extracts, 1);

    await writeFile(binary, "tampered binary");
    await ensureBinary(options);
    assert.equal(downloads, 2, "tampered executable must invalidate cache");
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});
test("default ZIP extraction rejects traversal and symlink members before writing", async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-zip-safety-"));
  try {
    for (const malicious of [
      storedZip([{ name: "../outside", data: "escape" }, { name: "apostate", data: "binary" }]),
      storedZip([{ name: "link", data: "apostate", mode: 0o120777 }, { name: "apostate", data: "binary" }]),
    ]) {
      const manifest = releaseManifest(malicious, "windows-x64");
      await assert.rejects(
        ensureBinary({ target: "windows-x64", cacheDir, manifest, download: async () => malicious }),
        (error) => error instanceof BinaryExtractionError,
      );
    }
    assert.equal(await readFile(join(cacheDir, "outside"), "utf8").catch(() => null), null);
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test("default tar extraction rejects link members before writing", async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-tar-safety-"));
  try {
    const malicious = tarArchive([
      { name: "apostate", data: "binary" },
      { name: "link", type: "2", linkname: "../outside" },
    ]);
    const manifest = releaseManifest(malicious, target);
    await assert.rejects(
      ensureBinary({ target, cacheDir, manifest, download: async () => malicious }),
      (error) => error instanceof BinaryExtractionError,
    );
    assert.equal(await readFile(join(cacheDir, "outside"), "utf8").catch(() => null), null);
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test("rejects traversal paths returned by an extractor", async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-traversal-"));
  try {
    const archive = Buffer.from("archive bytes");
    const manifest = releaseManifest(archive);
    await assert.rejects(
      ensureBinary({
        target,
        cacheDir,
        manifest,
        download: async () => archive,
        extract: async () => "../outside",
      }),
      (error) => error instanceof BinaryExtractionError,
    );
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test("launch reports an unpublished package before fabricating a browser", async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-launch-"));
  try {
    await assert.rejects(
      launch({ target, cacheDir, geoip: false, fingerprint: "host" }),
      (error) => error instanceof UnpublishedArtifactError,
    );
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});
