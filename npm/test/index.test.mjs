import assert from "node:assert/strict";
import { createHash, createPrivateKey, createPublicKey, sign } from "node:crypto";
import { copyFile, chmod, mkdir, readFile, symlink, writeFile } from "node:fs/promises";
import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import test from "node:test";

import {
  BinaryExtractionError,
  BinarySignatureError,
  CHROMIUM_VERSION,
  ProfileResolutionError,
  UnpublishedArtifactError,
  canonicalManifestBytes,
  ensureBinary,
  launch,
  resolveProfile,
  toCanonicalLaunchConfig,
  verifyArtifact,
} from "../dist/index.js";

const target = "linux-x64";
const artifactNameFor = (platform) => `apostate-${CHROMIUM_VERSION}-${platform}.${platform === "windows-x64" ? "zip" : "tar.zst"}`;
const artifactName = artifactNameFor(target);
const privateKey = createPrivateKey({
  key: Buffer.concat([
    Buffer.from("302e020100300506032b657004220420", "hex"),
    Buffer.from("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60", "hex"),
  ]),
  format: "der",
  type: "pkcs8",
});
const publicKey = createPublicKey(privateKey).export({ format: "pem", type: "spki" });

function signedManifest(archive, platform = target) {
  const artifact = artifactNameFor(platform);
  const manifest = {
    package_version: "0.1.0",
    chromium_version: CHROMIUM_VERSION,
    catalogue_version: 1,
    platform,
    artifact,
    sha256: createHash("sha256").update(archive).digest("hex"),
    url: `https://example.invalid/${artifact}`,
    signature: null,
  };
  const signature = sign(null, canonicalManifestBytes(manifest), privateKey).toString("base64");
  return { ...manifest, signature };
}

function artifactFor(manifest, platform = target) {
  return {
    target: platform,
    platform,
    artifact: manifest.artifact,
    sha256: manifest.sha256,
    signature: manifest.signature,
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


function fixtureFamily(id = "fixture-family", profilePlatform = "macOS") {
  return {
    id,
    platform: "macos",
    gpu_family: "apple-metal",
    evidence_class: "compatibility-capture",
    provenance: "fixture provenance",
    anchor: { renderer: "compatibility-capture", source: "catalogue-value" },
    profile: {
      id,
      source_capture: "fixture-capture",
      platform: { name: profilePlatform },
    },
  };
}

function fixtureCatalogue(index, extra = {}) {
  return {
    catalogue_version: 1,
    profile_schema_version: 2,
    browser_build: CHROMIUM_VERSION,
    families: [index],
    ...extra,
  };
}

async function writeCatalogueFixture(root, index, envelope, { writeFamily = true, catalogue = {} } = {}) {
  await mkdir(join(root, "families"), { recursive: true });
  await writeFile(join(root, "catalogue.json"), JSON.stringify(fixtureCatalogue(index, catalogue)));
  if (writeFamily) await writeFile(join(root, "families", `${index.id}.json`), JSON.stringify(envelope));
  return join(root, "catalogue.json");
}
async function stageAuthoritativeCatalogue(root) {
  const assetRoot = join(new URL("../assets/", import.meta.url).pathname);
  const catalogue = JSON.parse(await readFile(join(assetRoot, "catalogue.json"), "utf8"));
  await mkdir(join(root, "families"), { recursive: true });
  await writeFile(join(root, "catalogue.json"), JSON.stringify(catalogue));
  await copyFile(join(assetRoot, "compatibility-acceptance.json"), join(root, "compatibility-acceptance.json"));
  await copyFile(join(assetRoot, "compatibility-acceptance.schema.json"), join(root, "compatibility-acceptance.schema.json"));
  for (const family of catalogue.families) {
    await copyFile(join(assetRoot, family.file), join(root, family.file));
  }
  return { cataloguePath: join(root, "catalogue.json"), acceptancePath: join(root, "compatibility-acceptance.json") };
}


test("imports built ESM entrypoint and emits policy canonical bytes", async () => {
  const bytes = Buffer.from(canonicalManifestBytes({ z: "e\u00e9", a: 1 }));
  assert.equal(bytes.toString("utf8"), '{"a":1,"z":"e\\u00e9"}\n');
  const declaration = await readFile(new URL("../dist/index.d.ts", import.meta.url), "utf8");
  assert.match(declaration, /launchPersistentContext/);
});

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
test("default resolution exposes acceptance diagnostics without polluting native profile", () => {
  const resolved = resolveProfile({ fingerprintPlatform: "macos", fingerprint: "acceptance-default" });
  assert.equal(resolved.family.acceptance_track, "V3-C/V4-C");
  assert.equal(resolved.family.acceptance_status, "offered");
  assert.equal(resolved.family.validation_status, "unvalidated");
  assert.equal(resolved.family.acceptance_record_id, resolved.profileId);
  assert.equal(resolved.family.compatibility_acceptance.status, "offered");
  assert.equal(resolved.family.compatibility_acceptance.record_id, resolved.profileId);
  assert.equal(Object.hasOwn(resolved.profile, "compatibility_acceptance"), false);
  assert.equal(Object.hasOwn(resolved.profile, "acceptance_track"), false);
  assert.equal(Object.hasOwn(resolved.profile, "validation_status"), false);
});

test("rejects malformed nested acceptance source and surface records", async () => {
  for (const mutation of [
    (acceptance) => { acceptance.families[0].source.collector_sha256 = "invalid"; },
    (acceptance) => { acceptance.families[0].surfaces.webgl.identity.coverage = "invalid"; },
  ]) {
    const root = await mkdtemp(join(tmpdir(), "apostate-node-acceptance-tamper-"));
    try {
      const staged = await stageAuthoritativeCatalogue(root);
      const acceptance = JSON.parse(await readFile(staged.acceptancePath, "utf8"));
      mutation(acceptance);
      await writeFile(staged.acceptancePath, JSON.stringify(acceptance));
      assert.throws(
        () => resolveProfile({ cataloguePath: staged.cataloguePath, fingerprintPlatform: "macos", fingerprint: "tampered" }),
        ProfileResolutionError,
      );
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  }
});

test("resolves a shipped family envelope deterministically", () => {
  const first = resolveProfile({ fingerprintPlatform: "macos", fingerprint: "seed:stable-01" });
  const second = resolveProfile({ fingerprintPlatform: "macos", fingerprint: "seed:stable-01" });
  assert.match(first.profileId, /^apple-metal-/);
  assert.equal(first.profileId, second.profileId);
  assert.equal(first.identity, second.identity);
  assert.equal(first.profile.platform.name, "macOS");
});

test("confines catalogue family files to direct non-symlink JSON assets", async () => {
  const root = await mkdtemp(join(tmpdir(), "apostate-node-catalogue-paths-"));
  const id = "fixture-family";
  const envelope = fixtureFamily(id);
  const familyIndex = { id, file: `families/${id}.json`, platform: "macos", gpu_family: "apple-metal", evidence_class: "compatibility-capture" };
  try {
    for (const file of [
      join(root, "families", `${id}.json`),
      `../families/${id}.json`,
      `families/../families/${id}.json`,
      `families/nested/${id}.json`,
      `families/${id}.txt`,
    ]) {
      const cataloguePath = await writeCatalogueFixture(root, { ...familyIndex, file }, envelope);
      assert.throws(
        () => resolveProfile({ cataloguePath, fingerprintPlatform: "macos", fingerprint: "fixture" }),
        ProfileResolutionError,
      );
    }

    await rm(join(root, "families", `${id}.json`), { force: true });
    const missingCatalogue = await writeCatalogueFixture(root, familyIndex, envelope, { writeFamily: false });
    assert.throws(
      () => resolveProfile({ cataloguePath: missingCatalogue, fingerprintPlatform: "macos", fingerprint: "fixture" }),
      ProfileResolutionError,
    );

    const outside = join(root, "outside.json");
    await writeFile(outside, JSON.stringify(envelope));
    await symlink(outside, join(root, "families", `${id}.json`));
    const symlinkCatalogue = await writeCatalogueFixture(root, familyIndex, envelope, { writeFamily: false });
    assert.throws(
      () => resolveProfile({ cataloguePath: symlinkCatalogue, fingerprintPlatform: "macos", fingerprint: "fixture" }),
      ProfileResolutionError,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("binds family metadata and catalogue/profile schema versions", async () => {
  const root = await mkdtemp(join(tmpdir(), "apostate-node-catalogue-binding-"));
  const id = "fixture-family";
  const baseEnvelope = fixtureFamily(id);
  const baseIndex = { id, file: `families/${id}.json`, platform: "macos", gpu_family: "apple-metal", evidence_class: "compatibility-capture" };
  try {
    const cataloguePath = await writeCatalogueFixture(root, baseIndex, baseEnvelope);
    const resolved = resolveProfile({ cataloguePath, fingerprintPlatform: "macos", fingerprint: "fixture" });
    assert.equal(resolved.family.gpu_family, "apple-metal");
    assert.equal(resolved.family.evidence_class, "compatibility-capture");
    assert.equal(resolved.family.provenance, "fixture provenance");
    assert.deepEqual(resolved.family.anchor, baseEnvelope.anchor);
    assert.equal(resolved.profile.gpu_family, undefined);

    const mismatches = [
      [{ ...baseIndex, gpu_family: "intel-d3d11" }, baseEnvelope],
      [baseIndex, { ...baseEnvelope, evidence_class: "catalogue-value" }],
      [{ ...baseIndex, evidence_class: "bogus" }, baseEnvelope],
      [baseIndex, { ...baseEnvelope, evidence_class: "bogus" }],
      [baseIndex, { ...baseEnvelope, anchor: { renderer: "bogus" } }],
      [baseIndex, { ...baseEnvelope, provenance: "" }],
      [baseIndex, { ...baseEnvelope, anchor: {} }],
      [baseIndex, { ...baseEnvelope, id: "different-family" }],
      [baseIndex, { ...baseEnvelope, profile: { ...baseEnvelope.profile, id: "different-family" } }],
      [baseIndex, { ...baseEnvelope, profile: { ...baseEnvelope.profile, platform: { name: "Windows" } } }],
    ];
    for (const [index, envelope] of mismatches) {
      const mismatchCatalogue = await writeCatalogueFixture(root, index, envelope);
      assert.throws(
        () => resolveProfile({ cataloguePath: mismatchCatalogue, fingerprintPlatform: "macos", fingerprint: "fixture" }),
        ProfileResolutionError,
      );
    }

    const wrongSchemaCatalogue = await writeCatalogueFixture(root, baseIndex, baseEnvelope, { catalogue: { profile_schema_version: 99 } });
    assert.throws(
      () => resolveProfile({ cataloguePath: wrongSchemaCatalogue, fingerprintPlatform: "macos", fingerprint: "fixture" }),
      ProfileResolutionError,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
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

test("strips source_capture only from the native Node launch payload", async () => {
  const root = await mkdtemp(join(tmpdir(), "apostate-node-source-capture-"));
  const argvPath = join(root, "argv.json");
  const executable = join(root, "capture-argv.mjs");
  const profile = { id: "explicit", source_capture: "capture-2026-09-13", platform: { name: "macOS" } };
  try {
    await writeFile(executable, "#!/usr/bin/env node\nimport { writeFileSync } from \"node:fs\";\nwriteFileSync(process.env.APOSTATE_ARGV_PATH, JSON.stringify(process.argv.slice(2)));\nsetTimeout(() => {}, 10000);\n");
    await chmod(executable, 0o755);
    const browser = await launch({
      executablePath: executable,
      profile,
      fingerprintPlatform: "macos",
      geoip: false,
      headless: false,
      env: { APOSTATE_ARGV_PATH: argvPath },
    });
    try {
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
      const encoded = argv.find((value) => value.startsWith("--apostate-profile="));
      assert.ok(encoded);
      const payload = JSON.parse(Buffer.from(encoded.slice("--apostate-profile=".length), "base64").toString("utf8"));
      assert.equal(payload.id, "explicit");
      assert.equal(payload.source_capture, undefined);
      assert.equal(browser.launchConfig.profile.source_capture, profile.source_capture);
    } finally {
      await browser.close();
    }
  } finally {
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
          catalogue_version: 1,
          artifacts: {},
          signature: null,
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

test("rejects an invalid Ed25519 signature before extraction", async () => {
  const archive = Buffer.from("archive bytes");
  const manifest = signedManifest(archive);
  const artifact = artifactFor(manifest);
  await assert.rejects(
    verifyArtifact(archive, { ...manifest, signature: "A".repeat(88) }, { ...artifact, signature: "A".repeat(88) }, { publicKey }),
    (error) => error instanceof BinarySignatureError,
  );
});

test("accepts scalar release manifest and rejects tampered cache state", async () => {
  const cacheDir = await mkdtemp(join(tmpdir(), "apostate-node-cache-"));
  try {
    const archive = Buffer.from("archive bytes");
    const manifest = signedManifest(archive);
    let downloads = 0;
    let extracts = 0;
    const options = {
      target,
      cacheDir,
      manifest,
      publicKey,
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

    const metadata = join(cacheDir, CHROMIUM_VERSION, target, "binary-info.json");
    const metadataValue = JSON.parse(await readFile(metadata, "utf8"));
    metadataValue.signature = "A".repeat(88);
    await writeFile(metadata, JSON.stringify(metadataValue));
    await ensureBinary(options);
    assert.equal(downloads, 3, "tampered cache metadata must invalidate cache");
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
      const manifest = signedManifest(malicious, "windows-x64");
      await assert.rejects(
        ensureBinary({ target: "windows-x64", cacheDir, manifest, publicKey, download: async () => malicious }),
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
    const manifest = signedManifest(malicious, target);
    await assert.rejects(
      ensureBinary({ target, cacheDir, manifest, publicKey, download: async () => malicious }),
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
    const manifest = signedManifest(archive);
    await assert.rejects(
      ensureBinary({
        target,
        cacheDir,
        manifest,
        publicKey,
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
      launch({ target, cacheDir, geoip: false }),
      (error) => error instanceof UnpublishedArtifactError,
    );
  } finally {
    await rm(cacheDir, { recursive: true, force: true });
  }
});
