// @ts-nocheck
import {
  createHash,
  createPublicKey,
  verify as verifyEd25519,
} from "node:crypto";
import { spawn } from "node:child_process";
import {
  chmod,
  copyFile,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rename,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { existsSync, lstatSync, readFileSync as readFileSyncNative } from "node:fs";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { homedir, platform as hostPlatform, arch as hostArch } from "node:os";
import { fileURLToPath } from "node:url";

export const PACKAGE_VERSION = "0.1.0";
export const CHROMIUM_VERSION = "152.0.7977.83";
export const CATALOGUE_VERSION = 1;
const PROFILE_SCHEMA_VERSION = 2;
const SUPPORTED_EVIDENCE = {
  "physical-ground-truth": true,
  "compatibility-capture": true,
  "catalogue-value": true,
  "native-derived": true,
  "proxy-derived": true,
  "host-inherited": true,
};
function validateAnchor(anchor, label) {
  if (!isObject(anchor) || Object.keys(anchor).length === 0) {
    throw new ProfileResolutionError(`Profile family ${label} is missing anchor metadata.`);
  }
  for (const [field, evidence] of Object.entries(anchor)) {
    if (!field || typeof evidence !== "string" ||
        !Object.prototype.hasOwnProperty.call(SUPPORTED_EVIDENCE, evidence)) {
      throw new ProfileResolutionError(`Profile family ${label} anchor ${field} has an invalid evidence class.`);
    }
  }
}

const PACKAGE_ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const ASSET_ROOT = join(PACKAGE_ROOT, "assets");
const DEFAULT_MANIFEST_PATH = join(ASSET_ROOT, "release-manifest.json");
const DEFAULT_CATALOGUE_PATH = join(ASSET_ROOT, "catalogue.json");
const DEFAULT_PUBLIC_KEY_PATH = join(ASSET_ROOT, "public-key.txt");
const EXPECTED_TARGETS = new Set([
  "linux-x64",
  "linux-arm64",
  "macos-arm64",
  "windows-x64",
]);
const DEFAULT_PROFILE_SCHEMA_PATH = join(ASSET_ROOT, "profile.schema.json");
const DEFAULT_ACCEPTANCE_PATH = join(ASSET_ROOT, "compatibility-acceptance.json");
const DEFAULT_ACCEPTANCE_SCHEMA_PATH = join(ASSET_ROOT, "compatibility-acceptance.schema.json");
const ACCEPTANCE_FILE = "compatibility-acceptance.json";
const ACCEPTANCE_SCHEMA_DESCRIPTOR = "ledger/schema/compatibility-acceptance.schema.json";
const ACCEPTANCE_SCHEMA_FILE = "compatibility-acceptance.schema.json";
const ACCEPTANCE_TRACK = "V3-C/V4-C";
const ACCEPTANCE_STATUSES = new Set(["offered", "validated", "provisional", "limited"]);

const TOP_LEVEL_PROFILE_KEYS = new Set([
  "id",
  "source_capture",
  "cpu",
  "memory",
  "platform",
  "browser",
  "speech",
  "screen",
  "gl_limits",
  "gl_extensions",
  "gl_precisions",
  "webgpu",
  "gpu",
  "locale",
  "theme",
  "input",
  "audio",
  "media",
  "keyboard",
  "fonts",
]);
const NESTED_PROFILE_KEYS = new Map([
  ["cpu", ["logical_cores"]],
  ["memory", ["total_bytes"]],
  ["platform", ["name", "version", "architecture", "bitness", "model", "mobile", "navigator_platform"]],
  ["browser", ["user_agent"]],
  ["speech", ["voices"]],
  ["screen", ["displays", "is_extended", "width", "height", "avail_width", "avail_height", "device_pixel_ratio", "color_depth", "avail_left", "avail_top", "color_gamut", "hdr"]],
  ["webgpu", ["features", "info", "limits"]],
  ["gpu", ["unmasked_renderer", "unmasked_vendor"]],
  ["locale", ["timezone", "accept_languages"]],
  ["theme", ["prefers_dark", "highlight_argb", "highlight_text_argb", "system_fonts"]],
  ["input", ["pointer_type", "hover"]],
  ["audio", ["hardware_buffer_frames"]],
  ["media", ["hw_decode_codecs", "audioinput_count", "videoinput_count"]],
  ["keyboard", ["layout_map"]],
  ["fonts", ["generic_family_map"]],
]);

const PERSONA_ALIASES = new Map([
  ["darwin", "macos"],
  ["mac", "macos"],
  ["macos", "macos"],
  ["mac os", "macos"],
  ["mac os x", "macos"],
  ["os x", "macos"],
  ["osx", "macos"],
  ["win", "windows"],
  ["windows", "windows"],
  ["win32", "windows"],
  ["linux", "linux"],
]);

const TARGET_ARTIFACTS = {
  "linux-x64": `apostate-${CHROMIUM_VERSION}-linux-x64.tar.zst`,
  "linux-arm64": `apostate-${CHROMIUM_VERSION}-linux-arm64.tar.zst`,
  "macos-arm64": `apostate-${CHROMIUM_VERSION}-macos-arm64.tar.zst`,
  "windows-x64": `apostate-${CHROMIUM_VERSION}-windows-x64.zip`,
};

export class ApostateError extends Error {
  constructor(message, code = "APOSTATE_ERROR", details = {}) {
    super(message);
    this.name = "ApostateError";
    this.code = code;
    this.details = details;
  }
}

export class UnsupportedPlatformError extends ApostateError {
  constructor(platform, architecture) {
    super(
      `Apostate does not ship a binary for host ${platform}/${architecture}; supported targets are ${[...EXPECTED_TARGETS].join(", ")}.`,
      "UNSUPPORTED_PLATFORM",
      { platform, architecture },
    );
    this.name = "UnsupportedPlatformError";
  }
}

export class ProfileResolutionError extends ApostateError {
  constructor(message, details = {}) {
    super(message, "PROFILE_RESOLUTION_FAILED", details);
    this.name = "ProfileResolutionError";
  }
}

export class ManifestError extends ApostateError {
  constructor(message, details = {}) {
    super(message, "INVALID_RELEASE_MANIFEST", details);
    this.name = "ManifestError";
  }
}

export class UnpublishedArtifactError extends ManifestError {
  constructor(message = "Release manifest is unpublished; Apostate binary artifacts are not available for acquisition.", details = {}) {
    super(message, details);
    this.name = "UnpublishedArtifactError";
    this.code = "UNPUBLISHED_ARTIFACT";
  }
}

export class MissingBinaryError extends ApostateError {
  constructor(message, details = {}) {
    super(message, "BINARY_NOT_FOUND", details);
    this.name = "MissingBinaryError";
  }
}

export class BinaryIntegrityError extends ApostateError {
  constructor(message, details = {}) {
    super(message, "BINARY_HASH_MISMATCH", details);
    this.name = "BinaryIntegrityError";
  }
}

export class BinarySignatureError extends ApostateError {
  constructor(message, details = {}) {
    super(message, "BINARY_SIGNATURE_INVALID", details);
    this.name = "BinarySignatureError";
  }
}

export class BinaryDownloadError extends ApostateError {
  constructor(message, details = {}) {
    super(message, "BINARY_DOWNLOAD_FAILED", details);
    this.name = "BinaryDownloadError";
  }
}

export class BinaryExtractionError extends ApostateError {
  constructor(message, details = {}) {
    super(message, "BINARY_EXTRACTION_FAILED", details);
    this.name = "BinaryExtractionError";
  }
}

export class GeoIPError extends ApostateError {
  constructor(message, details = {}) {
    super(message, "GEOIP_LOOKUP_FAILED", details);
    this.name = "GeoIPError";
  }
}

export class BrowserLaunchError extends ApostateError {
  constructor(message, details = {}) {
    super(message, "BROWSER_LAUNCH_FAILED", details);
    this.name = "BrowserLaunchError";
  }
}

export class UnsupportedFeatureError extends ApostateError {
  constructor(message, details = {}) {
    super(message, "UNSUPPORTED_FEATURE", details);
    this.name = "UnsupportedFeatureError";
  }
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function cloneJson(value) {
  try {
    return JSON.parse(JSON.stringify(value));
  } catch (error) {
    throw new ProfileResolutionError("Profile must contain JSON-serializable values.", {
      cause: error instanceof Error ? error.message : String(error),
    });
  }
}

function jsonAscii(value) {
  const encoded = JSON.stringify(value);
  if (encoded === undefined) return undefined;
  return encoded.replace(/[^\x00-\x7F]/g, (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`);
}

function stableValue(value) {
  if (value === null || typeof value !== "object") {
    return jsonAscii(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableValue(item)).join(",")}]`;
  }
  const keys = Object.keys(value).sort();
  return `{${keys.map((key) => `${jsonAscii(key)}:${stableValue(value[key])}`).join(",")}}`;
}

export function stableStringify(value) {
  const output = stableValue(value);
  if (output === undefined) {
    throw new TypeError("Cannot canonicalize undefined JSON");
  }
  return output;
}
export function canonicalManifestBytes(value) {
  return Buffer.from(`${stableStringify(value)}\n`, "utf8");
}

function assertJsonTree(value, path = "profile") {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new ProfileResolutionError(`${path} contains a non-finite number.`);
    }
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((entry, index) => assertJsonTree(entry, `${path}[${index}]`));
    return;
  }
  if (!isObject(value)) {
    throw new ProfileResolutionError(`${path} contains an unsupported value.`);
  }
  for (const [key, entry] of Object.entries(value)) {
    if (key === "__proto__" || key === "constructor" || key === "prototype") {
      throw new ProfileResolutionError(`${path} contains a forbidden key.`);
    }
    assertJsonTree(entry, `${path}.${key}`);
  }
}

function assertKnownKeys(object, allowed, path) {
  for (const key of Object.keys(object)) {
    if (!allowed.has(key)) {
      throw new ProfileResolutionError(`${path}.${key} is not part of the native profile schema.`);
    }
  }
}

let profileSchema;

function loadProfileSchema() {
  if (profileSchema) return profileSchema;
  try {
    profileSchema = JSON.parse(readFileSyncNative(DEFAULT_PROFILE_SCHEMA_PATH, "utf8"));
  } catch (error) {
    throw new ProfileResolutionError("Package profile schema is missing or invalid.", {
      cause: error instanceof Error ? error.message : String(error),
    });
  }
  return profileSchema;
}

function jsonTypeMatches(value, type) {
  if (Array.isArray(type)) return type.some((entry) => jsonTypeMatches(value, entry));
  if (type === "object") return isObject(value);
  if (type === "array") return Array.isArray(value);
  if (type === "string") return typeof value === "string";
  if (type === "boolean") return typeof value === "boolean";
  if (type === "integer") return typeof value === "number" && Number.isInteger(value);
  if (type === "number") return typeof value === "number" && Number.isFinite(value);
  if (type === "null") return value === null;
  return true;
}

function schemaReference(root, reference) {
  if (typeof reference !== "string" || !reference.startsWith("#/")) return null;
  let current = root;
  for (const encoded of reference.slice(2).split("/")) {
    const key = encoded.replaceAll("~1", "/").replaceAll("~0", "~");
    if (!isObject(current) || !Object.prototype.hasOwnProperty.call(current, key)) return null;
    current = current[key];
  }
  return isObject(current) ? current : null;
}

function schemaMatches(value, schema, root) {
  try {
    schemaValidate(value, schema, "$schema-match", root);
    return true;
  } catch {
    return false;
  }
}

function schemaValidate(value, schema, path, root = schema) {
  if (!schema || typeof schema !== "object") return;
  if (schema.$ref !== undefined) {
    const referenced = schemaReference(root, schema.$ref);
    if (!referenced) throw new ProfileResolutionError(`${path} has an invalid schema reference.`);
    schemaValidate(value, referenced, path, root);
  }
  if (schema.allOf) {
    for (const branch of schema.allOf) schemaValidate(value, branch, path, root);
  }
  for (const keyword of ["anyOf", "oneOf"]) {
    if (!Array.isArray(schema[keyword]) || schema[keyword].length === 0) continue;
    const matches = schema[keyword].filter((branch) => schemaMatches(value, branch, root)).length;
    const valid = keyword === "anyOf" ? matches > 0 : matches === 1;
    if (!valid) throw new ProfileResolutionError(`${path} must match ${keyword === "anyOf" ? "at least one" : "exactly one"} schema branch.`);
  }
  if (schema.not && schemaMatches(value, schema.not, root)) {
    throw new ProfileResolutionError(`${path} must not match the schema branch.`);
  }
  if (schema.type && !jsonTypeMatches(value, schema.type)) {
    throw new ProfileResolutionError(`${path} must be ${Array.isArray(schema.type) ? schema.type.join(" or ") : schema.type}.`);
  }
  if (schema.const !== undefined && stableStringify(value) !== stableStringify(schema.const)) {
    throw new ProfileResolutionError(`${path} must equal the schema constant.`);
  }
  if (schema.enum && !schema.enum.some((entry) => stableStringify(entry) === stableStringify(value))) {
    throw new ProfileResolutionError(`${path} is not an allowed value.`);
  }
  if (typeof value === "string") {
    if (schema.minLength !== undefined && value.length < schema.minLength) throw new ProfileResolutionError(`${path} is shorter than the schema minimum.`);
    if (schema.maxLength !== undefined && value.length > schema.maxLength) throw new ProfileResolutionError(`${path} is longer than the schema maximum.`);
    if (schema.pattern && !(new RegExp(schema.pattern).test(value))) throw new ProfileResolutionError(`${path} does not match the schema pattern.`);
  }
  if (typeof value === "number") {
    if (schema.minimum !== undefined && value < schema.minimum) throw new ProfileResolutionError(`${path} is below the schema minimum.`);
    if (schema.maximum !== undefined && value > schema.maximum) throw new ProfileResolutionError(`${path} is above the schema maximum.`);
    if (schema.exclusiveMinimum !== undefined && value <= schema.exclusiveMinimum) throw new ProfileResolutionError(`${path} is not above the schema minimum.`);
    if (schema.exclusiveMaximum !== undefined && value >= schema.exclusiveMaximum) throw new ProfileResolutionError(`${path} is not below the schema maximum.`);
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) throw new ProfileResolutionError(`${path} has too few items.`);
    if (schema.maxItems !== undefined && value.length > schema.maxItems) throw new ProfileResolutionError(`${path} has too many items.`);
    if (schema.uniqueItems) {
      const unique = new Set(value.map((entry) => stableStringify(entry)));
      if (unique.size !== value.length) throw new ProfileResolutionError(`${path} contains duplicate items.`);
    }
    if (schema.items) value.forEach((entry, index) => schemaValidate(entry, schema.items, `${path}[${index}]`, root));
    if (schema.contains) {
      const count = value.filter((entry) => schemaMatches(entry, schema.contains, root)).length;
      if (count < (schema.minContains ?? 1) || (schema.maxContains !== undefined && count > schema.maxContains)) {
        throw new ProfileResolutionError(`${path} does not satisfy its contains constraint.`);
      }
    }
  }
  if (isObject(value)) {
    if (schema.required) {
      for (const required of schema.required) {
        if (!Object.prototype.hasOwnProperty.call(value, required)) throw new ProfileResolutionError(`${path}.${required} is required.`);
      }
    }
    if (schema.propertyNames) {
      for (const key of Object.keys(value)) schemaValidate(key, schema.propertyNames, `${path} property name`, root);
    }
    const properties = schema.properties ?? {};
    for (const [key, entry] of Object.entries(value)) {
      if (Object.prototype.hasOwnProperty.call(properties, key)) {
        schemaValidate(entry, properties[key], `${path}.${key}`, root);
      } else if (schema.additionalProperties === false) {
        throw new ProfileResolutionError(`${path}.${key} is not part of the schema.`);
      } else if (schema.additionalProperties && typeof schema.additionalProperties === "object") {
        schemaValidate(entry, schema.additionalProperties, `${path}.${key}`, root);
      }
    }
  }
  if (schema.if) {
    const branch = schemaMatches(value, schema.if, root) ? schema.then : schema.else;
    if (branch) schemaValidate(value, branch, path, root);
  }
}


export function validateProfile(profile) {
  if (!isObject(profile)) {
    throw new ProfileResolutionError("Profile must be a JSON object.");
  }
  schemaValidate(profile, loadProfileSchema(), "profile");
  assertJsonTree(profile);
  assertKnownKeys(profile, TOP_LEVEL_PROFILE_KEYS, "profile");
  for (const [section, keys] of NESTED_PROFILE_KEYS) {
    if (profile[section] !== undefined) {
      if (!isObject(profile[section])) {
        throw new ProfileResolutionError(`profile.${section} must be an object.`);
      }
      assertKnownKeys(profile[section], new Set(keys), `profile.${section}`);
    }
  }
  if (profile.id !== undefined && (typeof profile.id !== "string" || profile.id.length === 0)) {
    throw new ProfileResolutionError("profile.id must be a non-empty string.");
  }
  if (profile.locale) {
    for (const key of ["timezone", "accept_languages"]) {
      if (profile.locale[key] !== undefined && typeof profile.locale[key] !== "string") {
        throw new ProfileResolutionError(`profile.locale.${key} must be a string.`);
      }
    }
  }
  if (profile.platform) {
    for (const key of ["name", "version", "architecture", "bitness", "model", "navigator_platform"]) {
      if (profile.platform[key] !== undefined && typeof profile.platform[key] !== "string" && typeof profile.platform[key] !== "boolean") {
        throw new ProfileResolutionError(`profile.platform.${key} must be a scalar.`);
      }
    }
  }
  return cloneJson(profile);
}

function hostPersona() {
  if (hostPlatform() === "darwin") return "macos";
  if (hostPlatform() === "win32") return "windows";
  if (hostPlatform() === "linux") return "linux";
  return null;
}

export function normalizePersona(value) {
  if (value === undefined || value === null || value === "") {
    return hostPersona();
  }
  const normalized = PERSONA_ALIASES.get(String(value).trim().toLowerCase());
  if (!normalized) {
    throw new ProfileResolutionError(`Unsupported fingerprint platform ${String(value)}; use windows, macos, or linux.`);
  }
  return normalized;
}

export function targetForHost(platform = hostPlatform(), architecture = hostArch()) {
  if (platform === "darwin" && architecture === "arm64") return "macos-arm64";
  if (platform === "linux" && architecture === "x64") return "linux-x64";
  if (platform === "linux" && architecture === "arm64") return "linux-arm64";
  if (platform === "win32" && architecture === "x64") return "windows-x64";
  throw new UnsupportedPlatformError(platform, architecture);
}

export function normalizeTarget(target) {
  if (target === undefined || target === null || target === "") {
    return targetForHost();
  }
  const value = String(target).toLowerCase();
  if (!EXPECTED_TARGETS.has(value)) {
    throw new UnsupportedPlatformError(value, "unknown");
  }
  return value;
}

function validateOptionalSchemaVersions(value, source) {
  for (const [key, expected] of [["catalogue_version", CATALOGUE_VERSION], ["profile_schema_version", PROFILE_SCHEMA_VERSION]]) {
    if (value[key] !== undefined && value[key] !== expected) {
      throw new ProfileResolutionError(`${source}.${key} does not match this package.`, { expected, actual: value[key] });
    }
  }
}

function normalizedProfilePlatform(profile, source = "profile") {
  const platform = profile?.platform;
  if (platform === undefined || platform === null) return null;
  if (!isObject(platform)) throw new ProfileResolutionError(`${source}.platform must be an object.`);
  const name = platform.name;
  if (name === undefined || name === null || name === "") return null;
  if (typeof name !== "string") throw new ProfileResolutionError(`${source}.platform.name must be a string.`);
  return normalizePersona(name);
}

function requestedProfilePlatform(profile, requestedPlatform, source) {
  const declared = normalizedProfilePlatform(profile, source);
  if (requestedPlatform === undefined || requestedPlatform === null || requestedPlatform === "") {
    return declared ?? hostPersona();
  }
  const requested = normalizePersona(requestedPlatform);
  if (declared !== null && declared !== requested) {
    throw new ProfileResolutionError(`${source} platform ${declared} does not match fingerprint platform ${requested}.`, {
      requested_platform: requested,
      declared_platform: declared,
    });
  }
  return requested;
}

function familyPathFor(cataloguePath, family) {
  const id = family.id;
  const file = family.file;
  if (typeof file !== "string" || file.length === 0) {
    throw new ProfileResolutionError(`Profile family ${id} must declare a direct families/${id}.json file.`);
  }
  if (typeof id !== "string" || id.length === 0 || id.includes("/") || id.includes("\\") || id === "." || id === "..") {
    throw new ProfileResolutionError(`Profile family ${String(id)} has an unsafe identifier.`);
  }
  const expected = `families/${id}.json`;
  const absolute = isAbsolute(file) || /^[A-Za-z]:[\\/]/.test(file) || file.startsWith("\\\\") || file.startsWith("//");
  const parts = file.split("/");
  if (absolute || file.includes("\\") || parts.some((part) => part === "" || part === "." || part === "..") || file !== expected || !file.endsWith(".json")) {
    throw new ProfileResolutionError(`Profile family ${id} file must be the confined direct path ${expected}.`);
  }
  const catalogueDir = dirname(cataloguePath);
  const familyDir = join(catalogueDir, "families");
  const familyPath = join(familyDir, `${id}.json`);
  if (!withinDirectory(catalogueDir, familyPath)) {
    throw new ProfileResolutionError(`Profile family ${id} escapes the package catalogue directory.`);
  }
  try {
    if (lstatSync(familyDir).isSymbolicLink()) {
      throw new ProfileResolutionError(`Profile family directory for ${id} must not be a symlink.`);
    }
    const info = lstatSync(familyPath);
    if (info.isSymbolicLink()) {
      throw new ProfileResolutionError(`Profile family ${id} file must not be a symlink.`);
    }
    if (!info.isFile()) {
      throw new ProfileResolutionError(`Profile family ${id} file is missing: ${expected}.`);
    }
  } catch (error) {
    if (error instanceof ProfileResolutionError) throw error;
    throw new ProfileResolutionError(`Profile family ${id} file is missing: ${expected}.`, { cause: error?.message ?? String(error) });
  }
  return familyPath;
}

function familyMetadata(family) {
  if (!family) return null;
  const metadata = {
    id: family.id,
    platform: family.platform,
    gpu_family: family.gpu_family,
    evidence_class: family.evidence_class,
    provenance: family.provenance,
    anchor: cloneJson(family.anchor),
    ...(family.catalogue_version === undefined ? {} : { catalogue_version: family.catalogue_version }),
    ...(family.profile_schema_version === undefined ? {} : { profile_schema_version: family.profile_schema_version }),
  };
  const acceptance = family.acceptance;
  if (isObject(acceptance) && isObject(acceptance.binding)) {
    metadata.compatibility_acceptance = cloneJson(acceptance.binding);
    metadata.acceptance_track = acceptance.track;
    metadata.acceptance_status = acceptance.binding.status;
    metadata.validation_status = acceptance.binding.validation_status;
    metadata.acceptance_record_id = acceptance.binding.record_id;
  }
  return metadata;
}

function validateFamilyBinding(index, envelope, normalizedPlatform) {
  for (const [field, label] of [["gpu_family", "GPU family"], ["evidence_class", "evidence class"]]) {
    if (typeof index[field] !== "string" || index[field].trim() === "") {
      throw new ProfileResolutionError(`Profile family ${index.id} index ${field} must be a non-empty string.`);
    }
    if (typeof envelope[field] !== "string" || envelope[field].trim() === "") {
      throw new ProfileResolutionError(`Profile family ${index.id} file ${field} must be a non-empty string.`);
    }
    if (field === "evidence_class" &&
        !Object.prototype.hasOwnProperty.call(SUPPORTED_EVIDENCE, index[field])) {
      throw new ProfileResolutionError(`Profile family ${index.id} index evidence_class is invalid.`);
    }
    if (field === "evidence_class" &&
        !Object.prototype.hasOwnProperty.call(SUPPORTED_EVIDENCE, envelope[field])) {
      throw new ProfileResolutionError(`Profile family ${index.id} file evidence_class is invalid.`);
    }
    if (envelope[field] !== index[field]) {
      throw new ProfileResolutionError(`Profile family ${index.id} ${label} does not match catalogue metadata.`);
    }
  }
  if (typeof envelope.platform !== "string" || envelope.platform.trim() === "") {
    throw new ProfileResolutionError(`Profile family ${index.id} file platform must be a non-empty string.`);
  }
  if (envelope.id !== index.id || normalizePersona(envelope.platform) !== normalizedPlatform) {
    throw new ProfileResolutionError(`Profile family envelope ${index.id} does not match catalogue metadata.`);
  }
  if (typeof envelope.provenance !== "string" || envelope.provenance.trim() === "") {
    throw new ProfileResolutionError(`Profile family ${index.id} is missing provenance.`);
  }
  validateAnchor(envelope.anchor, index.id);
  if (!isObject(envelope.profile) || envelope.profile.id !== index.id) {
    throw new ProfileResolutionError(`Profile family ${index.id} profile identity does not match catalogue metadata.`);
  }
  if (normalizedProfilePlatform(envelope.profile, `Profile family ${index.id} profile`) !== normalizedPlatform) {
    throw new ProfileResolutionError(`Profile family ${index.id} profile platform does not match catalogue metadata.`);
  }
  if (index.provenance !== undefined &&
      (typeof index.provenance !== "string" || index.provenance.trim() === "")) {
    throw new ProfileResolutionError(`Profile family ${index.id} index provenance must be non-empty.`);
  }
  if (index.anchor !== undefined) {
    validateAnchor(index.anchor, `${index.id} index`);
    if (stableStringify(index.anchor) !== stableStringify(envelope.anchor)) {
      throw new ProfileResolutionError(`Profile family ${index.id} anchor does not match catalogue metadata.`);
    }
  }
  if (index.provenance !== undefined && index.provenance !== envelope.provenance) {
    throw new ProfileResolutionError(`Profile family ${index.id} provenance does not match catalogue metadata.`);
  }
}
function exactObject(value, expected) {
  return isObject(value) && Object.keys(value).length === expected.length && expected.every((key) => Object.prototype.hasOwnProperty.call(value, key));
}

function readJsonAsset(path, label) {
  try {
    const value = JSON.parse(readFileSyncNative(path, "utf8"));
    if (!isObject(value)) throw new Error("expected an object");
    return value;
  } catch (error) {
    throw new ProfileResolutionError(`${label} is unavailable or invalid.`, {
      path,
      cause: error instanceof Error ? error.message : String(error),
    });
  }
}

function rejectRawAcceptance(value, path = "compatibility_acceptance") {
  const rawKeys = new Set(["raw_capture", "raw_capture_path", "raw_corpus", "raw_corpus_path", "raw_data", "raw_payload"]);
  if (Array.isArray(value)) {
    value.forEach((entry, index) => rejectRawAcceptance(entry, `${path}[${index}]`));
    return;
  }
  if (typeof value === "string") {
    const normalized = value.replaceAll("\\", "/").toLowerCase();
    if (normalized === "raw" || normalized.startsWith("raw/") || normalized.includes("/raw/")) {
      throw new ProfileResolutionError(`${path} contains a forbidden raw capture path.`);
    }
    return;
  }
  if (!isObject(value)) return;
  for (const [key, entry] of Object.entries(value)) {
    if (rawKeys.has(key.toLowerCase())) throw new ProfileResolutionError(`${path}.${key} contains forbidden raw compatibility data.`);
    rejectRawAcceptance(entry, `${path}.${key}`);
  }
}

function sha256Hex(value) {
  return createHash("sha256").update(value).digest("hex");
}

function familySourceDigest(cataloguePath, families) {
  const lines = [...families]
    .sort((left, right) => left.file.localeCompare(right.file))
    .map((family) => `${sha256Hex(readFileSyncNative(familyPathFor(cataloguePath, family)))}  ${family.file}\n`)
    .join("");
  return sha256Hex(lines);
}

function acceptanceBinding(catalogue) {
  const binding = catalogue.compatibility_acceptance;
  if (binding === undefined) return null;
  const expected = {
    file: ACCEPTANCE_FILE,
    schema: ACCEPTANCE_SCHEMA_DESCRIPTOR,
    track: ACCEPTANCE_TRACK,
    initial_status: "offered",
  };
  if (!exactObject(binding, Object.keys(expected)) || Object.entries(expected).some(([key, value]) => binding[key] !== value)) {
    throw new ProfileResolutionError("profile catalogue compatibility_acceptance binding is invalid.");
  }
  return binding;
}

function loadAcceptance(catalogue, cataloguePath) {
  const binding = acceptanceBinding(catalogue);
  if (!binding) return null;
  const defaultCatalogue = resolve(DEFAULT_CATALOGUE_PATH);
  const acceptancePath = cataloguePath === defaultCatalogue ? DEFAULT_ACCEPTANCE_PATH : join(dirname(cataloguePath), ACCEPTANCE_FILE);
  const schemaPath = cataloguePath === defaultCatalogue ? DEFAULT_ACCEPTANCE_SCHEMA_PATH : join(dirname(cataloguePath), ACCEPTANCE_SCHEMA_FILE);
  const acceptance = readJsonAsset(acceptancePath, "compatibility acceptance index");
  const schema = readJsonAsset(schemaPath, "compatibility acceptance schema");
  try {
    schemaValidate(acceptance, schema, "compatibility_acceptance");
  } catch (error) {
    if (error instanceof ProfileResolutionError) {
      throw new ProfileResolutionError("compatibility acceptance index is malformed.", { cause: error.message });
    }
    throw error;
  }
  rejectRawAcceptance(acceptance);
  for (const field of ["catalogue_id", "catalogue_version", "profile_schema_version", "browser_build"]) {
    if (acceptance[field] !== catalogue[field]) {
      throw new ProfileResolutionError(`compatibility acceptance ${field} does not match profile catalogue.`);
    }
  }
  if (catalogue.version !== undefined && catalogue.version !== catalogue.catalogue_version) {
    throw new ProfileResolutionError("profile catalogue version does not match catalogue_version.");
  }
  if (acceptance.acceptance_track !== binding.track || acceptance.offering_policy.initial_catalogue_status !== binding.initial_status) {
    throw new ProfileResolutionError("compatibility acceptance policy does not match profile catalogue binding.");
  }
  const indexedFamilies = catalogue.families;
  if (!Array.isArray(indexedFamilies) || indexedFamilies.length === 0) {
    throw new ProfileResolutionError("profile catalogue families must be a non-empty list.");
  }
  const indexed = new Map();
  for (const entry of indexedFamilies) {
    if (!isObject(entry) || typeof entry.id !== "string" || entry.id.length === 0 || indexed.has(entry.id)) {
      throw new ProfileResolutionError("profile catalogue contains duplicate or invalid family IDs.");
    }
    indexed.set(entry.id, entry);
  }
  if (catalogue.family_count !== indexed.size || acceptance.capture_inventory.normalized_named_families !== indexed.size) {
    throw new ProfileResolutionError("compatibility acceptance family count does not match profile catalogue.");
  }
  if (catalogue.capture_count !== acceptance.capture_inventory.reported_compatibility_captures) {
    throw new ProfileResolutionError("compatibility acceptance capture count does not match profile catalogue.");
  }
  const catalogueInventory = catalogue.capture_inventory;
  if (!isObject(catalogueInventory) || acceptance.capture_inventory.collector_sha256 !== catalogueInventory.collector_sha256) {
    throw new ProfileResolutionError("compatibility acceptance collector does not match profile catalogue.");
  }
  if (!Array.isArray(acceptance.families)) {
    throw new ProfileResolutionError("compatibility acceptance families must be a list.");
  }
  const records = new Map();
  for (const record of acceptance.families) {
    if (!isObject(record) || typeof record.family_id !== "string" || records.has(record.family_id)) {
      throw new ProfileResolutionError("compatibility acceptance contains duplicate or invalid family IDs.");
    }
    records.set(record.family_id, record);
  }
  if (records.size !== indexed.size || [...records.keys()].some((id) => !indexed.has(id))) {
    throw new ProfileResolutionError("compatibility acceptance families do not match profile catalogue.");
  }
  for (const [familyId, entry] of indexed) {
    const record = records.get(familyId);
    if (record.platform !== entry.platform || record.evidence_class !== entry.evidence_class) {
      throw new ProfileResolutionError(`compatibility acceptance family ${familyId} metadata does not match profile catalogue.`);
    }
    if (!isObject(record.source) || record.source.runtime !== "gologin" || record.source.physical_reference !== false ||
        record.source.browser_build !== acceptance.browser_build || record.source.collector_sha256 !== acceptance.capture_inventory.collector_sha256) {
      throw new ProfileResolutionError(`compatibility acceptance family ${familyId} source metadata is invalid.`);
    }
    if (!ACCEPTANCE_STATUSES.has(record.status)) {
      throw new ProfileResolutionError(`compatibility acceptance family ${familyId} status is invalid.`);
    }
    const expectedBinding = {
      status: record.status,
      validation_status: record.status === "offered" ? "unvalidated" : record.status,
      record_id: familyId,
    };
    if (!exactObject(entry.acceptance, Object.keys(expectedBinding)) ||
        Object.entries(expectedBinding).some(([key, value]) => entry.acceptance[key] !== value)) {
      throw new ProfileResolutionError(`profile catalogue family ${familyId} acceptance binding disagrees with its record.`);
    }
  }
  const sourceDigest = familySourceDigest(cataloguePath, indexedFamilies);
  for (const [familyId, record] of records) {
    if (record.source.normalized_source_sha256 !== sourceDigest) {
      throw new ProfileResolutionError(`compatibility acceptance family ${familyId} source digest does not match family files.`);
    }
  }
  return {
    track: acceptance.acceptance_track,
    records: Object.fromEntries([...records].map(([id, record]) => [id, cloneJson(record)])),
  };
}

function loadCatalogue(path = DEFAULT_CATALOGUE_PATH) {
  const cataloguePath = resolve(path);
  let parsed;
  try {
    parsed = JSON.parse(readFileSyncNative(cataloguePath, "utf8"));
  } catch (error) {
    throw new ProfileResolutionError(`Unable to read profile catalogue ${cataloguePath}.`, {
      cause: error instanceof Error ? error.message : String(error),
    });
  }
  const browserBuild = parsed?.browser_build ?? parsed?.chromium_version;
  if (!isObject(parsed) || parsed.catalogue_version !== CATALOGUE_VERSION || browserBuild !== CHROMIUM_VERSION || !Array.isArray(parsed.families)) {
    throw new ProfileResolutionError("Profile catalogue version or shape does not match this package.", {
      expected_catalogue_version: CATALOGUE_VERSION,
      expected_chromium_version: CHROMIUM_VERSION,
    });
  }
  validateOptionalSchemaVersions(parsed, "profile catalogue");
  if (parsed.version !== undefined && parsed.version !== CATALOGUE_VERSION) {
    throw new ProfileResolutionError("profile catalogue.version does not match this package.", { expected: CATALOGUE_VERSION, actual: parsed.version });
  }
  const acceptance = loadAcceptance(parsed, cataloguePath);
  const families = parsed.families.map((family) => {
    if (!isObject(family) || typeof family.id !== "string" || family.id.trim() === "" || typeof family.platform !== "string" || family.platform.trim() === "") {
      throw new ProfileResolutionError("Profile catalogue contains an invalid family envelope.");
    }
    validateOptionalSchemaVersions(family, `profile catalogue family ${family.id}`);
    const platform = normalizePersona(family.platform);
    const familyPath = familyPathFor(cataloguePath, family);
    let envelope;
    try {
      envelope = JSON.parse(readFileSyncNative(familyPath, "utf8"));
    } catch (error) {
      throw new ProfileResolutionError(`Unable to read profile family ${family.id}.`, {
        cause: error instanceof Error ? error.message : String(error),
      });
    }
    if (!isObject(envelope)) {
      throw new ProfileResolutionError(`Profile family ${family.id} file must contain a JSON object.`);
    }
    validateOptionalSchemaVersions(envelope, `profile family ${family.id}`);
    validateFamilyBinding(family, envelope, platform);
    const profile = validateProfile(envelope.profile);
    const metadata = {
      id: family.id,
      platform,
      gpu_family: family.gpu_family,
      evidence_class: family.evidence_class,
      provenance: envelope.provenance,
      anchor: cloneJson(envelope.anchor),
      ...(family.catalogue_version === undefined && envelope.catalogue_version === undefined ? {} : { catalogue_version: family.catalogue_version ?? envelope.catalogue_version }),
      ...(family.profile_schema_version === undefined && envelope.profile_schema_version === undefined ? {} : { profile_schema_version: family.profile_schema_version ?? envelope.profile_schema_version }),
    };
    const acceptanceRecord = acceptance?.records?.[family.id];
    const familyAcceptance = acceptanceRecord ? {
      track: acceptance.track,
      binding: cloneJson(family.acceptance),
    } : undefined;
    return {
      id: family.id,
      platform,
      gpu_family: family.gpu_family,
      evidence_class: family.evidence_class,
      provenance: envelope.provenance,
      anchor: cloneJson(envelope.anchor),
      ...(family.catalogue_version === undefined && envelope.catalogue_version === undefined ? {} : { catalogue_version: family.catalogue_version ?? envelope.catalogue_version }),
      ...(family.profile_schema_version === undefined && envelope.profile_schema_version === undefined ? {} : { profile_schema_version: family.profile_schema_version ?? envelope.profile_schema_version }),
      metadata,
      profile,
      ...(familyAcceptance ? { acceptance: familyAcceptance } : {}),
    };
  }).sort((left, right) => left.id.localeCompare(right.id));
  return { catalogue_version: parsed.catalogue_version, profile_schema_version: parsed.profile_schema_version, chromium_version: browserBuild, families };
}

function readProfileFile(path) {
  const absolute = resolve(path);
  let parsed;
  try {
    parsed = JSON.parse(readFileSyncNative(absolute, "utf8"));
  } catch (error) {
    throw new ProfileResolutionError(`Unable to read profile file ${absolute}.`, {
      cause: error instanceof Error ? error.message : String(error),
    });
  }
  return validateProfile(parsed);
}

function looksLikePath(value) {
  return value.endsWith(".json") || value.includes("/") || value.includes("\\") || value.startsWith(".") || isAbsolute(value);
}

function normalizeSeed(seed) {
  if (typeof seed === "number") {
    if (!Number.isSafeInteger(seed) || seed < 0) {
      throw new ProfileResolutionError("fingerprint must be a non-negative safe integer or a stable non-empty string.");
    }
    return seed;
  }
  if (typeof seed === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(seed)) return seed;
  throw new ProfileResolutionError("fingerprint must be a non-negative safe integer or a stable non-empty string.");
}

function selectorDigest(seed, persona) {
  const selector = {
    seed,
    platform: persona,
    catalogue_version: CATALOGUE_VERSION,
    browser_build: CHROMIUM_VERSION,
  };
  return createHash("sha256").update(stableStringify(selector), "utf8").digest();
}

function profileIdentity(profileId, fingerprint, persona) {
  const identity = {
    profile_id: profileId,
    fingerprint: fingerprint ?? null,
    platform: persona,
    catalogue_version: CATALOGUE_VERSION,
    browser_build: CHROMIUM_VERSION,
  };
  return createHash("sha256").update(stableStringify(identity), "utf8").digest("hex");
}

function selectFamily(families, persona, seed) {
  const candidates = families.filter((family) => family.platform === persona).sort((left, right) => left.id.localeCompare(right.id));
  if (candidates.length === 0) return null;
  if (seed === undefined || seed === null) return candidates[0];
  const digest = selectorDigest(seed, persona);
  const index = Number(digest.readBigUInt64BE(0) % BigInt(candidates.length));
  return candidates[index];
}

function selectionResult(profile, source, profileId, fingerprint, persona, family = null) {
  const result = {
    profile: validateProfile(profile),
    source,
    profileId,
    identity: profileIdentity(profileId, fingerprint, persona),
    catalogueVersion: CATALOGUE_VERSION,
    chromiumVersion: CHROMIUM_VERSION,
  };
  if (family) result.family = familyMetadata(family);
  return result;
}

export function resolveProfile(options = {}) {
  if (!isObject(options)) throw new ProfileResolutionError("Launch options must be an object.");
  const requestedPlatform = options.fingerprintPlatform ?? options.fingerprint_platform;
  const explicitPath = options.profilePath ?? options.profile_file ?? options.profileFile;
  if (explicitPath !== undefined && explicitPath !== null) {
    const profile = readProfileFile(String(explicitPath));
    const persona = requestedProfilePlatform(profile, requestedPlatform, "Explicit profile");
    return selectionResult(profile, "explicit-file", profile.id ?? null, options.fingerprint, persona);
  }

  const explicitProfile = options.profile;
  if (isObject(explicitProfile)) {
    const profile = validateProfile(explicitProfile);
    const persona = requestedProfilePlatform(profile, requestedPlatform, "Explicit profile");
    return selectionResult(profile, "explicit-profile", profile.id ?? null, options.fingerprint, persona);
  }
  if (typeof explicitProfile === "string" && looksLikePath(explicitProfile)) {
    const profile = readProfileFile(explicitProfile);
    const persona = requestedProfilePlatform(profile, requestedPlatform, "Explicit profile");
    return selectionResult(profile, "explicit-file", profile.id ?? null, options.fingerprint, persona);
  }

  const hasFingerprint = options.fingerprint !== undefined && options.fingerprint !== null;
  const requestedId = options.profileId ?? options.profile_id ?? (typeof explicitProfile === "string" ? explicitProfile : undefined);
  const hasExplicitSelection = requestedId !== undefined && requestedId !== null;
  if (!hasFingerprint && requestedPlatform === undefined && !hasExplicitSelection) {
    return {
      profile: null,
      source: "host-inherited",
      profileId: null,
      identity: null,
      catalogueVersion: CATALOGUE_VERSION,
      chromiumVersion: CHROMIUM_VERSION,
    };
  }

  const catalogue = loadCatalogue(options.cataloguePath ?? options.catalogue_path ?? DEFAULT_CATALOGUE_PATH);
  const persona = normalizePersona(requestedPlatform);
  if (!persona) {
    throw new ProfileResolutionError("A fingerprint or profile selection requires a supported fingerprint platform.");
  }
  if (hasExplicitSelection) {
    const family = catalogue.families.find((entry) => entry.id === String(requestedId));
    if (!family) {
      throw new ProfileResolutionError(`Profile ID ${String(requestedId)} is not present in catalogue version ${CATALOGUE_VERSION}.`);
    }
    if (family.platform !== persona) {
      throw new ProfileResolutionError(`Profile ${family.id} targets ${family.platform}, not ${persona}.`);
    }
    return selectionResult(family.profile, "explicit-id", family.id, options.fingerprint, persona, family);
  }

  const family = selectFamily(catalogue.families, persona, options.fingerprint);
  if (!family) {
    throw new ProfileResolutionError(`No profile family is available for ${persona} in catalogue version ${CATALOGUE_VERSION}.`);
  }
  return selectionResult(family.profile, hasFingerprint ? "fingerprint" : "platform-default", family.id, options.fingerprint, persona, family);
}


function normalizeProxy(proxy) {
  if (proxy === undefined || proxy === null || proxy === "") return null;
  if (typeof proxy === "object") {
    const server = proxy.server ?? proxy.proxyServer ?? proxy.proxy_server;
    if (typeof server !== "string" || server.length === 0) {
      throw new TypeError("proxy.server must be a non-empty URL.");
    }
    let parsed;
    try {
      parsed = new URL(server);
    } catch {
      throw new TypeError("proxy.server must be a valid URL.");
    }
    if (proxy.username !== undefined) parsed.username = String(proxy.username);
    if (proxy.password !== undefined) parsed.password = String(proxy.password);
    return parsed.toString();
  }
  if (typeof proxy !== "string") throw new TypeError("proxy must be a URL string or an object with server.");
  try {
    const parsed = new URL(proxy);
    if (!parsed.protocol || !parsed.hostname) throw new Error("missing host");
    return parsed.toString();
  } catch {
    throw new TypeError("proxy must be a valid URL.");
  }
}

export function redactProxy(proxy) {
  if (proxy === undefined || proxy === null || proxy === "") return null;
  try {
    const parsed = new URL(typeof proxy === "string" ? proxy : normalizeProxy(proxy));
    parsed.username = "";
    parsed.password = "";
    return `${parsed.protocol}//${parsed.host}${parsed.pathname !== "/" ? parsed.pathname : ""}${parsed.search}${parsed.hash}`;
  } catch {
    return "<redacted-proxy>";
  }
}

function proxyEndpoint(proxy) {
  if (!proxy) return null;
  const parsed = new URL(proxy);
  parsed.username = "";
  parsed.password = "";
  return `${parsed.protocol}//${parsed.host}${parsed.pathname !== "/" ? parsed.pathname : ""}${parsed.search}${parsed.hash}`;
}

function sanitizeErrorMessage(message, proxy) {
  let text = String(message);
  if (proxy) {
    text = text.split(proxy).join(redactProxy(proxy));
    try {
      const parsed = new URL(proxy);
      if (parsed.username || parsed.password) {
        text = text.split(decodeURIComponent(parsed.username)).join("<redacted>");
        text = text.split(decodeURIComponent(parsed.password)).join("<redacted>");
      }
    } catch {
      // Keep the generic redaction below for malformed values.
    }
  }
  return text.replace(/(https?:\/\/)[^\s/@:]+:[^\s/@]+@/gi, "$1<redacted>@");
}

function localeLanguages(locale) {
  const value = String(locale).trim();
  if (value.includes(",")) return value;
  const base = value.split("-")[0];
  return base && base.toLowerCase() !== value.toLowerCase() ? `${value},${base}` : value;
}

function profileLocale(profile) {
  return {
    locale: profile?.locale?.accept_languages?.split(",")[0]?.trim() || null,
    timezone: profile?.locale?.timezone || null,
  };
}

function withLocale(profile, locale, timezone) {
  if (!profile && locale === null && timezone === null) return null;
  const output = profile ? cloneJson(profile) : {};
  const section = isObject(output.locale) ? output.locale : {};
  if (locale !== null && locale !== undefined) section.accept_languages = localeLanguages(locale);
  if (timezone !== null && timezone !== undefined) section.timezone = String(timezone);
  output.locale = section;
  return validateProfile(output);
}

export function toCanonicalLaunchConfig(options = {}) {
  if (!isObject(options)) throw new TypeError("Launch options must be an object.");
  const rawFingerprint = options.fingerprint;
  if (rawFingerprint !== undefined && rawFingerprint !== null) normalizeSeed(rawFingerprint);
  const args = options.args ?? [];
  if (!Array.isArray(args) || args.some((arg) => typeof arg !== "string")) {
    throw new TypeError("args must be an array of strings.");
  }
  const userDataDir = options.userDataDir ?? options.user_data_dir ?? null;
  if (userDataDir !== null && typeof userDataDir !== "string") {
    throw new TypeError("userDataDir must be a path string.");
  }
  const locale = options.locale ?? options.fingerprintLocale ?? options.fingerprint_locale ?? null;
  const timezone = options.timezone ?? options.fingerprintTimezone ?? options.fingerprint_timezone ?? null;
  if (locale !== null && typeof locale !== "string") throw new TypeError("locale must be a string.");
  if (timezone !== null && typeof timezone !== "string") throw new TypeError("timezone must be a string.");
  const proxy = normalizeProxy(options.proxy);
  const fingerprintPlatform = options.fingerprintPlatform ?? options.fingerprint_platform ?? null;
  if (fingerprintPlatform !== null) normalizePersona(fingerprintPlatform);
  return {
    fingerprint: rawFingerprint ?? null,
    fingerprint_platform: fingerprintPlatform,
    profile: null,
    locale,
    timezone,
    geoip: options.geoip !== false,
    proxy,
    headless: options.headless !== false,
    user_data_dir: userDataDir,
    args: [...args],
  };
}

async function defaultGeoipLookup(url, proxy, signal) {
  if (proxy) {
    throw new GeoIPError(`GeoIP lookup through proxy ${redactProxy(proxy)} requires a proxy-aware geoipResolver.`, {
      proxy: redactProxy(proxy),
    });
  }
  if (typeof fetch !== "function") {
    throw new GeoIPError("geoip=true requires geoipResolver or a Node runtime with fetch.");
  }
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`GeoIP endpoint returned HTTP ${response.status}.`);
  return response.json();
}

function validateGeoipResult(result) {
  if (!isObject(result)) throw new GeoIPError("GeoIP resolver must return an object.");
  const locale = result.locale ?? result.language ?? result.languages?.[0] ?? null;
  const timezone = result.timezone ?? result.time_zone ?? null;
  if (locale !== null && typeof locale !== "string") throw new GeoIPError("GeoIP locale must be a string.");
  if (timezone !== null && typeof timezone !== "string") throw new GeoIPError("GeoIP timezone must be a string.");
  if (!locale || !timezone) throw new GeoIPError("GeoIP resolver did not provide both locale and timezone; refusing to invent defaults.");
  return { locale, timezone };
}

async function prepareLaunch(options = {}) {
  const canonical = toCanonicalLaunchConfig(options);
  const resolution = resolveProfile(options);
  let profile = resolution.profile;
  const inheritedLocale = profileLocale(profile);
  let geoipResult = null;
  if (canonical.geoip && (canonical.locale === null || canonical.timezone === null)) {
    const controller = new AbortController();
    const timeoutMs = Number.isFinite(options.geoipTimeoutMs) ? Math.max(1, options.geoipTimeoutMs) : 10000;
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const resolver = options.geoipResolver ?? (options.geoipUrl ? (context) => defaultGeoipLookup(options.geoipUrl, context.proxy, context.signal) : null);
      if (!resolver) {
        throw new GeoIPError("geoip=true requires geoipResolver or geoipUrl; refusing to invent locale/timezone.", {
          proxy: redactProxy(canonical.proxy),
        });
      }
      geoipResult = validateGeoipResult(await resolver({
        proxy: canonical.proxy,
        proxy_redacted: redactProxy(canonical.proxy),
        signal: controller.signal,
      }));
    } catch (error) {
      if (error instanceof GeoIPError) throw error;
      const reason = error?.name === "AbortError" ? "timed out" : sanitizeErrorMessage(error?.message ?? error, canonical.proxy);
      throw new GeoIPError(`GeoIP lookup ${reason}; launch was not started.`, {
        proxy: redactProxy(canonical.proxy),
      });
    } finally {
      clearTimeout(timeout);
    }
  }
  canonical.locale = canonical.locale ?? geoipResult?.locale ?? inheritedLocale.locale ?? null;
  canonical.timezone = canonical.timezone ?? geoipResult?.timezone ?? inheritedLocale.timezone ?? null;
  if (canonical.geoip && (canonical.locale === null || canonical.timezone === null)) {
    throw new GeoIPError("geoip=true could not resolve both locale and timezone; launch was not started.", {
      proxy: redactProxy(canonical.proxy),
    });
  }
  profile = withLocale(profile, canonical.locale, canonical.timezone);
  canonical.profile = profile;
  return {
    config: canonical,
    resolution,
    diagnostics: {
      proxy: redactProxy(canonical.proxy),
      geoip: canonical.geoip ? (geoipResult ? "resolved" : "explicit") : "disabled",
      profile_source: resolution.source,
      profile_id: resolution.profileId,
      profile_identity: resolution.identity,
      family: resolution.family ?? null,
      catalogue_version: CATALOGUE_VERSION,
      chromium_version: CHROMIUM_VERSION,
    },
  };
}

export async function resolveLaunchConfig(options = {}) {
  return (await prepareLaunch(options)).config;
}

function hasSwitch(args, name) {
  return args.some((arg) => arg === name || arg.startsWith(`${name}=`));
}

function stripSourceCapture(value) {
  if (Array.isArray(value)) return value.map((entry) => stripSourceCapture(entry));
  if (!isObject(value)) return value;
  const output = {};
  for (const [key, entry] of Object.entries(value)) {
    if (key !== "source_capture") output[key] = stripSourceCapture(entry);
  }
  return output;
}

function nativeProfilePayload(profile) {
  return validateProfile(stripSourceCapture(profile));
}

function buildLaunchArguments(config) {
  const args = config.args.filter((arg) => !arg.startsWith("--apostate-profile=") && !arg.startsWith("--proxy-server=") && !arg.startsWith("--user-data-dir="));
  if (config.profile !== null) {
    const encoded = Buffer.from(stableStringify(nativeProfilePayload(config.profile)), "utf8").toString("base64");
    args.push(`--apostate-profile=${encoded}`);
  }
  if (config.headless && !hasSwitch(args, "--headless")) args.push("--headless=new");
  if (config.user_data_dir !== null) args.push(`--user-data-dir=${config.user_data_dir}`);
  if (config.proxy !== null) args.push(`--proxy-server=${proxyEndpoint(config.proxy)}`);
  if (config.locale !== null && !hasSwitch(args, "--lang")) args.push(`--lang=${config.locale.split(",")[0]}`);
  return args;
}

async function isRegularFile(path) {
  try {
    const info = await stat(path);
    return info.isFile();
  } catch {
    return false;
  }
}

function defaultCacheDir() {
  return process.env.APOSTATE_CACHE_DIR || join(process.env.XDG_CACHE_HOME || join(homedir(), ".cache"), "apostate");
}

function expectedArtifactName(target) {
  return TARGET_ARTIFACTS[target];
}

function unpublishedArtifactError(manifest) {
  return new UnpublishedArtifactError(undefined, { status: manifest.status ?? "unpublished" });
}

function manifestDeclaresUnpublished(manifest) {
  if (manifest.status === "unpublished") return true;
  if (isObject(manifest.artifacts) && Object.keys(manifest.artifacts).length === 0 && manifest.artifact === undefined) return true;
  if (Array.isArray(manifest.artifacts) && manifest.artifacts.length === 0 && manifest.artifact === undefined) return true;
  return false;
}

function normalizeManifest(manifest, source = "manifest") {
  if (!isObject(manifest)) throw new ManifestError(`${source} must contain a JSON object.`);
  for (const [key, expected] of [["package_version", PACKAGE_VERSION], ["chromium_version", CHROMIUM_VERSION], ["catalogue_version", CATALOGUE_VERSION]]) {
    if (manifest[key] !== undefined && manifest[key] !== expected) {
      throw new ManifestError(`${source}.${key} does not match this package.`, { expected, actual: manifest[key] });
    }
  }
  if (manifest.artifacts !== undefined && !Array.isArray(manifest.artifacts) && !isObject(manifest.artifacts)) {
    throw new ManifestError(`${source}.artifacts must be an array or object.`);
  }
  return {
    ...cloneJson(manifest),
    package_version: manifest.package_version ?? PACKAGE_VERSION,
    chromium_version: manifest.chromium_version ?? CHROMIUM_VERSION,
    catalogue_version: manifest.catalogue_version ?? CATALOGUE_VERSION,
  };
}

async function readOrDownloadManifest(options = {}) {
  if (options.manifest !== undefined && options.manifest !== null) {
    if (typeof options.manifest === "string") {
      const source = options.manifest;
      if (/^[a-z][a-z\d+.-]*:\/\//i.test(source)) return normalizeManifest(await downloadUrl(source, options, "manifest"), source);
      try {
        return normalizeManifest(JSON.parse(await readFile(resolve(source), "utf8")), source);
      } catch (error) {
        if (error instanceof ManifestError) throw error;
        throw new ManifestError(`Unable to read release manifest ${source}.`, { cause: error?.message ?? String(error) });
      }
    }
    return normalizeManifest(options.manifest, "manifest option");
  }
  if (options.manifestPath) {
    try {
      return normalizeManifest(JSON.parse(await readFile(resolve(options.manifestPath), "utf8")), options.manifestPath);
    } catch (error) {
      if (error instanceof ManifestError) throw error;
      throw new ManifestError(`Unable to read release manifest ${options.manifestPath}.`, { cause: error?.message ?? String(error) });
    }
  }
  if (options.manifestUrl) return normalizeManifest(await downloadUrl(options.manifestUrl, options, "manifest"), options.manifestUrl);
  try {
    return normalizeManifest(JSON.parse(await readFile(DEFAULT_MANIFEST_PATH, "utf8")), DEFAULT_MANIFEST_PATH);
  } catch (error) {
    if (error instanceof ManifestError) throw error;
    throw new ManifestError(`Unable to read package release manifest ${DEFAULT_MANIFEST_PATH}.`, { cause: error?.message ?? String(error) });
  }
}

function artifactFromManifest(manifest, target) {
  const expected = expectedArtifactName(target);
  const artifacts = manifest.artifacts;
  let candidate = null;
  if (Array.isArray(artifacts)) {
    candidate = artifacts.find((entry) => isObject(entry) && (entry.target === target || entry.platform === target || entry.name === expected || entry.artifact === expected));
  } else if (isObject(artifacts)) {
    candidate = artifacts[target] ?? null;
  }
  if (!candidate && typeof manifest.artifact === "string" && manifest.platform === target) {
    candidate = {
      platform: manifest.platform,
      artifact: manifest.artifact,
      sha256: manifest.sha256,
      signature: manifest.signature,
      url: manifest.url ?? manifest.download_url,
      binary_path: manifest.binary_path ?? manifest.binaryPath,
    };
  }
  if (!candidate && isObject(manifest.artifact) && (manifest.artifact.target === undefined || manifest.artifact.platform === undefined || manifest.artifact.platform === target || manifest.artifact.target === target)) candidate = manifest.artifact;
  if (!candidate) return null;
  if (!isObject(candidate)) throw new ManifestError(`Manifest artifact for ${target} must be an object.`);
  const name = candidate.artifact ?? candidate.name ?? expected;
  if (name !== expected) {
    throw new ManifestError(`Manifest artifact name ${name} does not match expected ${expected}.`, { target, expected, actual: name });
  }
  if (candidate.platform !== undefined && candidate.platform !== target && candidate.target !== target) {
    throw new ManifestError(`Manifest artifact platform ${candidate.platform} does not match ${target}.`, { target, actual: candidate.platform });
  }
  return { ...cloneJson(candidate), target, platform: target, artifact: expected, name: expected };
}
async function downloadUrl(url, options, kind) {
  const proxy = normalizeProxy(options.proxy);
  if (proxy && !options.download) {
    throw new BinaryDownloadError(`Cannot download ${kind} through proxy ${redactProxy(proxy)} without a proxy-aware downloader.`, {
      proxy: redactProxy(proxy),
    });
  }
  if (options.download) {
    try {
      const value = await options.download(String(url), {
        kind,
        proxy,
        proxy_redacted: redactProxy(proxy),
      });
      if (kind === "manifest") {
        if (isObject(value)) return value;
        return JSON.parse(Buffer.from(await toBuffer(value)).toString("utf8"));
      }
      return await toBuffer(value);
    } catch (error) {
      if (error instanceof ApostateError) throw error;
      throw new BinaryDownloadError(`Unable to download ${kind}: ${sanitizeErrorMessage(error?.message ?? error, proxy)}.`, {
        proxy: redactProxy(proxy),
      });
    }
  }
  if (typeof fetch !== "function") throw new BinaryDownloadError("This Node runtime has no fetch; provide a download callback.");
  try {
    const timeoutMs = Number.isFinite(options.downloadTimeoutMs) ? Math.max(1, options.downloadTimeoutMs) : 30000;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(String(url), { signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      if (kind === "manifest") return await response.json();
      return Buffer.from(await response.arrayBuffer());
    } finally {
      clearTimeout(timer);
    }
  } catch (error) {
    const reason = error?.name === "AbortError" ? "timed out" : sanitizeErrorMessage(error?.message ?? error, proxy);
    throw new BinaryDownloadError(`Unable to download ${kind}: ${reason}.`, { proxy: redactProxy(proxy) });
  }
}

async function toBuffer(value) {
  if (Buffer.isBuffer(value)) return value;
  if (value instanceof Uint8Array) return Buffer.from(value);
  if (value instanceof ArrayBuffer) return Buffer.from(value);
  if (value && typeof value.arrayBuffer === "function") return Buffer.from(await value.arrayBuffer());
  if (value && value[Symbol.asyncIterator]) {
    const chunks = [];
    for await (const chunk of value) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    return Buffer.concat(chunks);
  }
  throw new TypeError("Downloader must return bytes, an ArrayBuffer, or an async iterable of bytes.");
}

function decodeSignature(value) {
  if (typeof value !== "string" || !/^[A-Za-z0-9+/]{86}==$/.test(value)) return null;
  const decoded = Buffer.from(value, "base64");
  return decoded.length === 64 ? decoded : null;
}

function publicKeyObject(value) {
  let source = value;
  if (source === undefined || source === null) {
    try {
      source = readFileSyncNative(DEFAULT_PUBLIC_KEY_PATH, "utf8");
    } catch (error) {
      throw new BinarySignatureError("Package public verification key is missing.", { cause: error?.message ?? String(error) });
    }
  } else if (typeof source === "string" && !source.includes("BEGIN PUBLIC KEY") && existsSync(source)) {
    source = readFileSyncNative(source, "utf8");
  }
  if (typeof source === "string" && /^[0-9a-f]{64}$/i.test(source.trim())) {
    source = Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), Buffer.from(source.trim(), "hex")]);
  }
  try {
    return createPublicKey(typeof source === "string" ? source.trim() : source);
  } catch (error) {
    throw new BinarySignatureError("Configured Ed25519 public verification key is invalid.", { cause: error?.message ?? String(error) });
  }
}

function clearArtifactSignature(document, target, artifact) {
  const output = cloneJson(document);
  if (Object.prototype.hasOwnProperty.call(output, "signature")) output.signature = null;
  if (Array.isArray(output.artifacts)) {
    const entry = output.artifacts.find((item) => isObject(item) && (item.target === target || item.artifact === artifact || item.name === artifact));
    if (entry && Object.prototype.hasOwnProperty.call(entry, "signature")) entry.signature = null;
  } else if (isObject(output.artifacts) && isObject(output.artifacts[target])) {
    if (Object.prototype.hasOwnProperty.call(output.artifacts[target], "signature")) output.artifacts[target].signature = null;
  } else if (isObject(output.artifact) && (output.artifact.target === undefined || output.artifact.target === target)) {
    if (Object.prototype.hasOwnProperty.call(output.artifact, "signature")) output.artifact.signature = null;
  }
  return output;
}

function signatureEntries(manifest, artifact, target) {
  const entries = [];
  if (typeof manifest.signature === "string") {
    entries.push({ signature: manifest.signature, document: { ...cloneJson(manifest), signature: null } });
  }
  if (isObject(manifest.signature) && typeof manifest.signature[target] === "string") {
    const copy = cloneJson(manifest);
    copy.signature = { ...copy.signature, [target]: null };
    entries.push({ signature: manifest.signature[target], document: copy });
  }
  if (typeof artifact.signature === "string") {
    entries.push({
      signature: artifact.signature,
      document: clearArtifactSignature(manifest, target, artifact.artifact),
    });
  }
  return entries;
}
function verifyManifestSignature(manifest, artifact, target, options = {}) {
  const entries = signatureEntries(manifest, artifact, target);
  if (entries.length === 0) {
    throw new BinarySignatureError(`Release manifest has no Ed25519 signature for ${artifact?.artifact ?? artifact?.name ?? "binary artifact"}.`);
  }
  const key = publicKeyObject(options.publicKey);
  for (const entry of entries) {
    const signature = decodeSignature(entry.signature);
    if (!signature || signature.length !== 64) continue;
    try {
      if (verifyEd25519(null, canonicalManifestBytes(entry.document), key, signature)) return true;
    } catch {
      // Invalid signatures are rejected after trying all supported record shapes.
    }
  }
  throw new BinarySignatureError(`Ed25519 signature rejected for ${artifact?.artifact ?? artifact?.name ?? "binary artifact"}.`);
}

export async function verifyArtifact(archive, manifest, artifact, options = {}) {
  const bytes = await toBuffer(archive);
  if (!artifact || typeof artifact.sha256 !== "string" || !/^[0-9a-f]{64}$/i.test(artifact.sha256)) {
    throw new BinaryIntegrityError("Release manifest is missing a valid SHA-256 for the artifact.");
  }
  const actualHash = createHash("sha256").update(bytes).digest("hex");
  if (actualHash.toLowerCase() !== artifact.sha256.toLowerCase()) {
    throw new BinaryIntegrityError(`SHA-256 mismatch for ${artifact.artifact ?? artifact.name ?? "binary artifact"}.`, {
      expected: artifact.sha256.toLowerCase(),
      actual: actualHash,
    });
  }
  verifyManifestSignature(manifest, artifact, artifact.target, options);
  return { sha256: actualHash, signature_verified: true };
}

async function runCommand(command, args, cwd, capture = false) {
  return new Promise((resolveCommand, rejectCommand) => {
    const child = spawn(command, args, { cwd, stdio: ["ignore", "pipe", "pipe"] });
    const stdout = [];
    const stderr = [];
    child.stdout?.on("data", (chunk) => stdout.push(Buffer.from(chunk)));
    child.stderr?.on("data", (chunk) => stderr.push(Buffer.from(chunk)));
    child.once("error", (error) => rejectCommand(error));
    child.once("close", (code) => {
      if (code === 0) return resolveCommand(capture ? Buffer.concat(stdout).toString("utf8") : undefined);
      const detail = Buffer.concat(stderr).toString("utf8").trim();
      rejectCommand(new Error(`${command} exited with code ${code}${detail ? `: ${detail}` : ""}`));
    });
  });
}

function safeArchiveMemberName(value) {
  const name = String(value).replaceAll("\\", "/");
  const parts = name.split("/");
  if (!name || name.includes("\0") || name.startsWith("/") || /^[A-Za-z]:\//.test(name) || parts.includes("..")) {
    throw new BinaryExtractionError(`Archive contains an unsafe member path: ${JSON.stringify(name)}.`);
  }
  return name;
}

function safeArchiveMode(mode, name) {
  const type = mode & 0o170000;
  if (type !== 0 && type !== 0o100000 && type !== 0o040000) {
    throw new BinaryExtractionError(`Archive member ${JSON.stringify(name)} is a link or special file.`);
  }
}

function scanZipArchive(bytes) {
  const minimumEnd = 22;
  const lowerBound = Math.max(0, bytes.length - 0xffff - minimumEnd);
  let endOffset = -1;
  for (let offset = bytes.length - minimumEnd; offset >= lowerBound; offset -= 1) {
    if (bytes.readUInt32LE(offset) === 0x06054b50) {
      endOffset = offset;
      break;
    }
  }
  if (endOffset < 0 || endOffset + minimumEnd > bytes.length) {
    throw new BinaryExtractionError("Unable to inspect ZIP central directory before extraction.");
  }
  const entryCount = bytes.readUInt16LE(endOffset + 10);
  const directorySize = bytes.readUInt32LE(endOffset + 12);
  const directoryOffset = bytes.readUInt32LE(endOffset + 16);
  if (entryCount === 0xffff || directorySize === 0xffffffff || directoryOffset === 0xffffffff || directoryOffset + directorySize > endOffset) {
    throw new BinaryExtractionError("ZIP64 archives are not supported for safe extraction.");
  }
  let offset = directoryOffset;
  for (let index = 0; index < entryCount; index += 1) {
    if (offset + 46 > bytes.length || bytes.readUInt32LE(offset) !== 0x02014b50) {
      throw new BinaryExtractionError("ZIP central directory is malformed.");
    }
    const versionMadeBy = bytes.readUInt16LE(offset + 4);
    const flags = bytes.readUInt16LE(offset + 8);
    const nameLength = bytes.readUInt16LE(offset + 28);
    const extraLength = bytes.readUInt16LE(offset + 30);
    const commentLength = bytes.readUInt16LE(offset + 32);
    const nameStart = offset + 46;
    const nextOffset = nameStart + nameLength + extraLength + commentLength;
    if (nextOffset > bytes.length || nextOffset > directoryOffset + directorySize) {
      throw new BinaryExtractionError("ZIP central directory entry is truncated.");
    }
    const name = bytes.subarray(nameStart, nameStart + nameLength).toString(flags & 0x0800 ? "utf8" : "latin1");
    safeArchiveMemberName(name);
    if ((versionMadeBy >>> 8) === 3) safeArchiveMode((bytes.readUInt32LE(offset + 38) >>> 16) & 0xffff, name);
    offset = nextOffset;
  }
  if (offset > directoryOffset + directorySize) throw new BinaryExtractionError("ZIP central directory extends beyond its declared size.");
}

async function runTarList(archivePath, destination, verbose) {
  const attempts = [
    ["--use-compress-program=zstd"],
    [],
  ];
  let firstError;
  for (const prefix of attempts) {
    try {
      return await runCommand("tar", [...prefix, verbose ? "-tvf" : "-tf", archivePath], destination, true);
    } catch (error) {
      firstError ??= error;
    }
  }
  throw new BinaryExtractionError(`Unable to inspect tar archive before extraction: ${firstError?.message ?? "tar failed"}.`);
}

async function scanTarArchive(archivePath, destination) {
  const names = (await runTarList(archivePath, destination, false)).split(/\r?\n/).filter((line) => line.length > 0);
  const details = (await runTarList(archivePath, destination, true)).split(/\r?\n/).filter((line) => line.length > 0);
  if (names.length !== details.length) throw new BinaryExtractionError("Tar archive listing is ambiguous; refusing extraction.");
  names.forEach((name, index) => {
    safeArchiveMemberName(name);
    const mode = details[index].slice(0, 10);
    if (!/^[\-dlhbcps][\-rwx]{9}(?:\s|$)/.test(details[index])) {
      throw new BinaryExtractionError("Tar archive member metadata is malformed.");
    }
    if (mode[0] !== "-" && mode[0] !== "d") safeArchiveMode(0o120000, name);
  });
}

async function scanArchive(bytes, archivePath, destination, context) {
  if (context.artifact.endsWith(".zip")) {
    scanZipArchive(bytes);
  } else if (context.artifact.endsWith(".tar.zst")) {
    await scanTarArchive(archivePath, destination);
  } else {
    throw new BinaryExtractionError(`unsupported archive format ${context.artifact}`);
  }
}


async function defaultExtract(bytes, destination, context) {
  const archivePath = join(destination, context.artifact);
  await writeFile(archivePath, bytes, { mode: 0o600 });
  try {
    await scanArchive(bytes, archivePath, destination, context);
    if (context.artifact.endsWith(".zip")) {
      await runCommand("unzip", ["-q", archivePath, "-d", destination], destination);
    } else if (context.artifact.endsWith(".tar.zst")) {
      try {
        await runCommand("tar", ["--use-compress-program=zstd", "-xf", archivePath, "-C", destination], destination);
      } catch (firstError) {
        await runCommand("tar", ["-xf", archivePath, "-C", destination], destination).catch(() => { throw firstError; });
      }
    } else {
      throw new Error(`unsupported archive format ${context.artifact}`);
    }
  } finally {
    await rm(archivePath, { force: true });
  }
}

function withinDirectory(root, candidate) {
  const relativePath = relative(resolve(root), resolve(candidate));
  return relativePath === "" || (!isAbsolute(relativePath) && relativePath !== ".." && !relativePath.startsWith(".." + sep));
}

async function findExtractedBinary(root, requestedPath, target) {
  if (requestedPath) {
    const candidate = resolve(root, requestedPath);
    if (!withinDirectory(root, candidate) || !(await isRegularFile(candidate))) {
      throw new BinaryExtractionError("Manifest binary_path does not identify a file inside the extracted archive.");
    }
    return candidate;
  }
  const names = target === "windows-x64" ? new Set(["apostate.exe", "apostate"]) : new Set(["apostate"]);
  const results = [];
  async function walk(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) await walk(path);
      else if (entry.isFile() && names.has(entry.name)) results.push(path);
    }
  }
  await walk(root);
  if (results.length !== 1) {
    throw new BinaryExtractionError(results.length === 0 ? "Extracted archive does not contain apostate executable." : "Extracted archive contains more than one apostate executable.");
  }
  return results[0];
}

function cachePaths(cacheDir, target) {
  const root = join(resolve(cacheDir), CHROMIUM_VERSION, target);
  return {
    root,
    archive: join(root, expectedArtifactName(target)),
    binary: join(root, target === "windows-x64" ? "apostate.exe" : "apostate"),
    metadata: join(root, "binary-info.json"),
  };
}

function missingBinary(target, cacheDir, proxy, reason) {
  const details = { target, cache_dir: resolve(cacheDir), proxy: redactProxy(proxy) };
  const suffix = reason ? ` ${reason}` : "";
  return new MissingBinaryError(`Apostate Chromium ${CHROMIUM_VERSION} for ${target} is not installed in ${details.cache_dir}.${suffix} Run ensureBinary() with a signed release manifest or provide executablePath.`, details);
}

function cacheSignature(manifest, artifact, target) {
  if (typeof artifact?.signature === "string") return artifact.signature;
  if (typeof manifest?.signature === "string") return manifest.signature;
  if (isObject(manifest?.signature) && typeof manifest.signature[target] === "string") return manifest.signature[target];
  return null;
}
async function validCachedBinary(paths, target, manifest, artifact, options = {}) {
  if (!(await isRegularFile(paths.archive)) || !(await isRegularFile(paths.binary)) || !(await isRegularFile(paths.metadata))) return false;
  try {
    const metadata = JSON.parse(await readFile(paths.metadata, "utf8"));
    const expectedSignature = cacheSignature(manifest, artifact, target);
    if (!isObject(metadata) || metadata.package_version !== PACKAGE_VERSION || metadata.chromium_version !== CHROMIUM_VERSION || metadata.catalogue_version !== CATALOGUE_VERSION) return false;
    if (metadata.target !== target || metadata.platform !== target || metadata.artifact !== expectedArtifactName(target)) return false;
    if (typeof artifact?.sha256 !== "string" || !/^[0-9a-f]{64}$/i.test(artifact.sha256)) return false;
    if (metadata.sha256?.toLowerCase() !== artifact.sha256.toLowerCase()) return false;
    if (typeof expectedSignature !== "string" || metadata.signature !== expectedSignature) return false;
    if (metadata.signature_verified !== true || decodeSignature(metadata.signature)?.length !== 64) return false;
    const archive = await readFile(paths.archive);
    await verifyArtifact(archive, manifest, artifact, options);
    const actualHash = createHash("sha256").update(await readFile(paths.binary)).digest("hex");
    return typeof metadata.binary_sha256 === "string" && actualHash.toLowerCase() === metadata.binary_sha256.toLowerCase();
  } catch {
    return false;
  }
}


export async function ensureBinary(options = {}) {
  if (typeof options === "string") options = { binaryPath: options };
  if (!isObject(options)) throw new TypeError("ensureBinary options must be an object.");
  const explicitBinary = options.binaryPath ?? options.executablePath;
  if (explicitBinary !== undefined && explicitBinary !== null) {
    const path = resolve(String(explicitBinary));
    if (!(await isRegularFile(path))) throw missingBinary(normalizeTarget(options.target), options.cacheDir ?? defaultCacheDir(), options.proxy, `The configured binary path ${path} does not exist.`);
    return path;
  }
  const target = normalizeTarget(options.target);
  const cacheDir = options.cacheDir ?? options.cache_dir ?? defaultCacheDir();
  const paths = cachePaths(cacheDir, target);
  const manifest = await readOrDownloadManifest(options);
  if (manifest.status === "unpublished") throw unpublishedArtifactError(manifest);
  const artifact = artifactFromManifest(manifest, target);
  if (!artifact) {
    if (manifestDeclaresUnpublished(manifest)) throw unpublishedArtifactError(manifest);
    throw missingBinary(target, cacheDir, options.proxy, "No signed artifact is listed in the release manifest.");
  }
  if (!options.force && await validCachedBinary(paths, target, manifest, artifact, options)) return paths.binary;
  let archive;
  try {
    if (artifact.path || artifact.local_path || artifact.file) {
      archive = await readFile(resolve(artifact.path ?? artifact.local_path ?? artifact.file));
    } else {
      const url = artifact.url ?? artifact.download_url ?? (manifest.base_url ? new URL(artifact.artifact, manifest.base_url).toString() : null);
      if (!url) throw new BinaryDownloadError(`Release manifest has no download URL for ${target}.`);
      archive = await downloadUrl(url, options, "binary artifact");
    }
  } catch (error) {
    if (error instanceof ApostateError) throw error;
    throw new BinaryDownloadError(`Unable to obtain ${artifact.artifact}: ${sanitizeErrorMessage(error?.message ?? error, normalizeProxy(options.proxy))}.`, { proxy: redactProxy(options.proxy) });
  }
  await verifyArtifact(archive, manifest, artifact, options);
  await mkdir(paths.root, { recursive: true, mode: 0o700 });
  const temporary = await mkdtemp(join(paths.root, ".extract-"));
  try {
    const extract = options.extract ?? defaultExtract;
    const result = await extract(archive, temporary, {
      artifact: artifact.artifact,
      target,
      manifest,
      proxy: normalizeProxy(options.proxy),
      proxy_redacted: redactProxy(options.proxy),
    });
    const candidate = await findExtractedBinary(temporary, typeof result === "string" ? result : artifact.binary_path ?? artifact.binaryPath, target);
    await rm(paths.binary, { force: true });
    await rename(candidate, paths.binary);
    await writeFile(paths.archive, archive, { mode: 0o600 });
    if (target !== "windows-x64") await chmod(paths.binary, 0o755);
    const signature = cacheSignature(manifest, artifact, target);
    if (!signature) throw new BinarySignatureError(`Release manifest has no Ed25519 signature for ${artifact.artifact}.`);
    const binarySha256 = createHash("sha256").update(await readFile(paths.binary)).digest("hex");
    await writeFile(paths.metadata, `${stableStringify({
      package_version: PACKAGE_VERSION,
      chromium_version: CHROMIUM_VERSION,
      catalogue_version: CATALOGUE_VERSION,
      target,
      platform: target,
      artifact: artifact.artifact,
      sha256: artifact.sha256.toLowerCase(),
      binary_sha256: binarySha256,
      signature,
      signature_verified: true,
    })}\n`, { mode: 0o600 });
    return paths.binary;
  } catch (error) {
    if (error instanceof ApostateError) throw error;
    throw new BinaryExtractionError(`Unable to extract ${artifact.artifact}: ${sanitizeErrorMessage(error?.message ?? error, options.proxy)}.`, { target });
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
}

export async function binaryInfo(options = {}) {
  if (!isObject(options)) throw new TypeError("binaryInfo options must be an object.");
  const target = normalizeTarget(options.target);
  const cacheDir = options.cacheDir ?? options.cache_dir ?? defaultCacheDir();
  const paths = cachePaths(cacheDir, target);
  const manifest = await readOrDownloadManifest(options);
  const artifact = artifactFromManifest(manifest, target);
  return {
    package_version: PACKAGE_VERSION,
    chromium_version: CHROMIUM_VERSION,
    catalogue_version: CATALOGUE_VERSION,
    target,
    artifact: artifact?.artifact ?? expectedArtifactName(target),
    artifact_url: artifact?.url ?? artifact?.download_url ?? null,
    sha256: artifact?.sha256 ?? null,
    signature_present: Boolean(typeof manifest.signature === "string" || (artifact && typeof artifact.signature === "string")),
    cache_dir: resolve(cacheDir),
    binary_path: paths.binary,
    cache_hit: artifact ? await validCachedBinary(paths, target, manifest, artifact, options) : false,
    available: Boolean(artifact),
  };
}

export async function clearCache(options = {}) {
  if (typeof options === "string") options = { cacheDir: options };
  if (!isObject(options)) throw new TypeError("clearCache options must be an object.");
  const cacheDir = options.cacheDir ?? options.cache_dir ?? defaultCacheDir();
  let path = resolve(cacheDir);
  if (options.target !== undefined && options.target !== null) path = join(path, CHROMIUM_VERSION, normalizeTarget(options.target));
  else if (options.version !== undefined && options.version !== null) path = join(path, String(options.version));
  await rm(path, { recursive: true, force: true });
}

function waitForExit(child, timeoutMs = 5000) {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
  return new Promise((resolveExit) => {
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      resolveExit();
    };
    const timer = setTimeout(finish, timeoutMs);
    child.once("exit", finish);
  });
}

function spawnBrowser(binary, args, options) {
  return new Promise((resolveBrowser, rejectBrowser) => {
    let child;
    try {
      child = spawn(binary, args, {
        cwd: options.cwd,
        env: options.env ? { ...process.env, ...options.env } : process.env,
        stdio: options.stdio ?? "ignore",
      });
    } catch (error) {
      rejectBrowser(new BrowserLaunchError(`Unable to launch Apostate Chromium: ${sanitizeErrorMessage(error?.message ?? error, options.proxy)}.`, { proxy: redactProxy(options.proxy) }));
      return;
    }
    let settled = false;
    const fail = (error) => {
      if (settled) return;
      settled = true;
      rejectBrowser(new BrowserLaunchError(`Unable to launch Apostate Chromium: ${sanitizeErrorMessage(error?.message ?? error, options.proxy)}.`, { proxy: redactProxy(options.proxy) }));
    };
    child.once("error", fail);
    child.once("spawn", () => {
      if (settled) return;
      settled = true;
      resolveBrowser(child);
    });
  });
}

export class ApostateBrowser {
  constructor(child, executablePath, launchConfig, diagnostics) {
    this.process = child;
    this.executablePath = executablePath;
    this.launchConfig = launchConfig;
    this.diagnostics = diagnostics;
    this._contexts = new Set();
    this._closed = false;
  }

  isConnected() {
    return !this._closed && this.process.exitCode === null && this.process.signalCode === null && !this.process.killed;
  }

  contexts() {
    return [...this._contexts];
  }

  async newContext(options = {}) {
    if (!this.isConnected()) throw new BrowserLaunchError("Browser is not connected.");
    if (!isObject(options)) throw new TypeError("Context options must be an object.");
    const context = new ApostateBrowserContext(this, { ...this.launchConfig, ...options }, false);
    this._contexts.add(context);
    return context;
  }

  async close() {
    if (this._closed) return;
    this._closed = true;
    if (this.process.exitCode === null && this.process.signalCode === null) {
      this.process.kill("SIGTERM");
      await waitForExit(this.process);
      if (this.process.exitCode === null && this.process.signalCode === null) this.process.kill("SIGKILL");
    }
    this._contexts.clear();
  }

  async newPage() {
    throw new UnsupportedFeatureError("The dependency-free package does not implement a browser protocol client; use a Patchright-compatible client against the launched process.");
  }
}

export class ApostateBrowserContext {
  constructor(browser, launchConfig, persistent) {
    this.browser = browser;
    this.launchConfig = launchConfig;
    this.persistent = persistent;
    this._closed = false;
  }

  isClosed() {
    return this._closed || !this.browser.isConnected();
  }

  async newPage() {
    throw new UnsupportedFeatureError("The dependency-free package does not implement a browser protocol client; use a Patchright-compatible client against the launched process.");
  }

  async close() {
    if (this._closed) return;
    this._closed = true;
    this.browser._contexts.delete(this);
    if (this.persistent) await this.browser.close();
  }
}

export async function launch(options = {}) {
  if (!isObject(options)) throw new TypeError("Launch options must be an object.");
  const prepared = await prepareLaunch(options);
  const binary = options.executablePath ?? options.binaryPath ?? await ensureBinary(options);
  const args = buildLaunchArguments(prepared.config);
  const child = await spawnBrowser(binary, args, options);
  return new ApostateBrowser(child, binary, prepared.config, prepared.diagnostics);
}

export async function launchContext(options = {}) {
  const browser = await launch(options);
  try {
    return await browser.newContext();
  } catch (error) {
    await browser.close();
    throw error;
  }
}

export async function launchPersistentContext(userDataDir, options = {}) {
  if (typeof userDataDir !== "string" || userDataDir.length === 0) throw new TypeError("userDataDir must be a non-empty path string.");
  if (!isObject(options)) throw new TypeError("Launch options must be an object.");
  if (options.userDataDir !== undefined && resolve(options.userDataDir) !== resolve(userDataDir)) {
    throw new TypeError("launchPersistentContext received conflicting userDataDir values.");
  }
  const browser = await launch({ ...options, userDataDir });
  return new ApostateBrowserContext(browser, browser.launchConfig, true);
}

// Python-style names are useful when sharing launch code across wrappers.
export const launch_context = launchContext;
export const launch_persistent_context = launchPersistentContext;
export const ensure_binary = ensureBinary;
export const binary_info = binaryInfo;
export const clear_cache = clearCache;
export const translateOptions = toCanonicalLaunchConfig;
