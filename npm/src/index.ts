// @ts-nocheck
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import {
  request as httpRequest,
} from "node:http";
import {
  request as httpsRequest,
} from "node:https";
import { isIP } from "node:net";
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
import { readFileSync as readFileSyncNative } from "node:fs";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { homedir, platform as hostPlatform, arch as hostArch } from "node:os";
import { fileURLToPath } from "node:url";
import { SocksProxyAgent } from "socks-proxy-agent";
import { HttpProxyAgent } from "http-proxy-agent";
import { HttpsProxyAgent } from "https-proxy-agent";
export const PACKAGE_VERSION = "0.1.0";
export const CHROMIUM_VERSION = "152.0.7977.83";
export const CATALOGUE_VERSION = 2;
const PROFILE_SCHEMA_VERSION = 3;
const SUPPORTED_EVIDENCE = {
  "physical-ground-truth": true,
  "compatibility-capture": true,
  "catalogue-value": true,
  "native-derived": true,
  "proxy-derived": true,
  "host-inherited": true,
};

const PACKAGE_ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const ASSET_ROOT = join(PACKAGE_ROOT, "assets");
const DEFAULT_MANIFEST_PATH = join(ASSET_ROOT, "release-manifest.json");
const DEFAULT_CATALOGUE_PATH = join(ASSET_ROOT, "catalogue.json");
const EXPECTED_TARGETS = new Set([
  "linux-x64",
  "linux-arm64",
  "macos-arm64",
  "windows-x64",
]);
const DEFAULT_PROFILE_SCHEMA_PATH = join(ASSET_ROOT, "profile.schema.json");
const CATALOGUE_MODEL = "anchors+dispersion";
// Catalogue version 1 shipped the fourteen-family model; version 2 retired it.
const RETIRED_CATALOGUE_KEYS = ["families", "family_count", "distributions"];
const CATALOGUE_POLICY_FAMILIES = { locale: true, theme: true };
const HOST_INHERITANCE_SEED = "host";
// docs/FINGERPRINTS.md section 7: the compositor is the browser process's and is
// the only implementation, so a seed or persona cannot be materialised here.
const COMPOSITION_UNAVAILABLE_MESSAGE = "composition happens in the browser process; the in-binary compositor is not yet wired to this package, so a fingerprint seed or platform persona cannot be materialised here; pass an explicit profile file or inline profile object, or request host inheritance with fingerprint \"host\"";
const HOST_PERSONA_MESSAGE = "host inheritance disables every layer below it, so a platform persona cannot be applied without the in-binary compositor";
const EXPLICIT_PROFILE_WARNING = "explicit profile bypasses composition; its coherence and servability are the author's responsibility, not the catalogue's";

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
  constructor(message, details = {}, code = "PROFILE_RESOLUTION_FAILED") {
    super(message, code, details);
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
  // config/profile.schema.json is the single source of truth for accepted
  // fields: every object in it is closed, so schemaValidate rejects unknown
  // keys at every level and a second hand-kept allow-list would only drift.
  schemaValidate(profile, loadProfileSchema(), "profile");
  assertJsonTree(profile);
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

function requireCatalogueString(value, label) {
  if (typeof value !== "string" || value.trim() === "") {
    throw new ProfileResolutionError(`${label} must be a non-empty string.`);
  }
  return value;
}

function catalogueAnchorView(anchor, position) {
  if (!isObject(anchor)) {
    throw new ProfileResolutionError(`profile catalogue anchors[${position}] must be an object.`);
  }
  const id = requireCatalogueString(anchor.id, `profile catalogue anchors[${position}].id`);
  if (!Object.prototype.hasOwnProperty.call(SUPPORTED_EVIDENCE, anchor.evidence_class)) {
    throw new ProfileResolutionError(`profile catalogue anchor ${id} evidence_class is invalid.`);
  }
  if (!Array.isArray(anchor.members) || anchor.members.length === 0) {
    throw new ProfileResolutionError(`profile catalogue anchor ${id} must list its measured members.`);
  }
  if (anchor.member_count !== undefined && anchor.member_count !== anchor.members.length) {
    throw new ProfileResolutionError(`profile catalogue anchor ${id} member_count does not match its members.`);
  }
  return {
    id,
    platform: normalizePersona(requireCatalogueString(anchor.platform, `profile catalogue anchor ${id} platform`)),
    backend: requireCatalogueString(anchor.backend, `profile catalogue anchor ${id} backend`),
    members: anchor.members.map((member, index) => requireCatalogueString(member, `profile catalogue anchor ${id} members[${index}]`)),
    rotation_status: requireCatalogueString(anchor.rotation_status, `profile catalogue anchor ${id} rotation_status`),
  };
}

function catalogueAxisView(axis, position) {
  if (!isObject(axis)) {
    throw new ProfileResolutionError(`profile catalogue axes[${position}] must be an object.`);
  }
  const label = requireCatalogueString(axis.axis, `profile catalogue axes[${position}].axis`);
  if (!Array.isArray(axis.conditioned_on)) {
    throw new ProfileResolutionError(`profile catalogue axis ${label} conditioned_on must be a list.`);
  }
  for (const count of ["option_sets", "options"]) {
    if (!Number.isInteger(axis[count]) || axis[count] < 1) {
      throw new ProfileResolutionError(`profile catalogue axis ${label} ${count} must be a positive integer.`);
    }
  }
  return {
    axis: label,
    selection: requireCatalogueString(axis.selection, `profile catalogue axis ${label} selection`),
    servability: requireCatalogueString(axis.servability, `profile catalogue axis ${label} servability`),
    conditioned_on: axis.conditioned_on.map((entry, index) => requireCatalogueString(entry, `profile catalogue axis ${label} conditioned_on[${index}]`)),
    option_sets: axis.option_sets,
    options: axis.options,
  };
}

function cataloguePolicyIds(policies, family) {
  const entries = policies[family];
  if (!Array.isArray(entries) || entries.length === 0) {
    throw new ProfileResolutionError(`profile catalogue policies.${family} must be a non-empty list.`);
  }
  return entries.map((entry, index) => {
    if (!isObject(entry)) {
      throw new ProfileResolutionError(`profile catalogue policies.${family}[${index}] must be an object.`);
    }
    return requireCatalogueString(entry.id, `profile catalogue policies.${family}[${index}].id`);
  });
}

// The catalogue describes the browser process's composition model. The package
// reads it to report the anchors, axes and policy ids it will compose from, and
// never to compose: see docs/FINGERPRINTS.md section 7.
export function loadCatalogue(path = DEFAULT_CATALOGUE_PATH) {
  const cataloguePath = resolve(path);
  let parsed;
  try {
    parsed = JSON.parse(readFileSyncNative(cataloguePath, "utf8"));
  } catch (error) {
    throw new ProfileResolutionError(`Unable to read profile catalogue ${cataloguePath}.`, {
      cause: error instanceof Error ? error.message : String(error),
    });
  }
  if (!isObject(parsed)) {
    throw new ProfileResolutionError(`Profile catalogue ${cataloguePath} must contain a JSON object.`);
  }
  for (const key of RETIRED_CATALOGUE_KEYS) {
    if (parsed[key] !== undefined) {
      throw new ProfileResolutionError(
        `profile catalogue still carries the retired ${key} key; catalogue version ${CATALOGUE_VERSION} composes from anchors and dispersion.`,
        { retired_key: key },
      );
    }
  }
  requireCatalogueString(parsed.catalogue_id, "profile catalogue catalogue_id");
  for (const [key, expected] of [
    ["catalogue_version", CATALOGUE_VERSION],
    ["profile_schema_version", PROFILE_SCHEMA_VERSION],
    ["browser_build", CHROMIUM_VERSION],
    ["model", CATALOGUE_MODEL],
  ]) {
    if (parsed[key] !== expected) {
      throw new ProfileResolutionError(`profile catalogue ${key} does not match this package.`, {
        expected,
        actual: parsed[key] ?? null,
      });
    }
  }
  if (parsed.version !== undefined && parsed.version !== CATALOGUE_VERSION) {
    throw new ProfileResolutionError("profile catalogue.version does not match catalogue_version.", {
      expected: CATALOGUE_VERSION,
      actual: parsed.version,
    });
  }
  if (!Array.isArray(parsed.anchors) || parsed.anchors.length === 0) {
    throw new ProfileResolutionError("profile catalogue anchors must be a non-empty list.");
  }
  if (!Array.isArray(parsed.axes) || parsed.axes.length === 0) {
    throw new ProfileResolutionError("profile catalogue axes must be a non-empty list.");
  }
  if (!isObject(parsed.policies)) {
    throw new ProfileResolutionError("profile catalogue policies must be an object.");
  }
  for (const family of Object.keys(parsed.policies)) {
    if (!Object.prototype.hasOwnProperty.call(CATALOGUE_POLICY_FAMILIES, family)) {
      throw new ProfileResolutionError(`profile catalogue policies.${family} is not a catalogue version ${CATALOGUE_VERSION} policy family.`);
    }
  }
  return {
    catalogue_version: parsed.catalogue_version,
    profile_schema_version: parsed.profile_schema_version,
    browser_build: parsed.browser_build,
    model: parsed.model,
    anchors: parsed.anchors.map(catalogueAnchorView),
    axes: parsed.axes.map(catalogueAxisView),
    policies: {
      locale: cataloguePolicyIds(parsed.policies, "locale"),
      theme: cataloguePolicyIds(parsed.policies, "theme"),
    },
  };
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

function explicitProfileResult(validated, source, fingerprint, persona) {
  const profileId = validated.id ?? null;
  return {
    profile: validated,
    source,
    profileId,
    identity: profileIdentity(profileId, fingerprint, persona),
    catalogueVersion: CATALOGUE_VERSION,
    chromiumVersion: CHROMIUM_VERSION,
    warnings: [EXPLICIT_PROFILE_WARNING],
  };
}

export function resolveProfile(options = {}) {
  if (!isObject(options)) throw new ProfileResolutionError("Launch options must be an object.");
  const requestedPlatform = options.fingerprintPlatform ?? options.fingerprint_platform;
  const explicitPath = options.profilePath ?? options.profile_file ?? options.profileFile;
  if (explicitPath !== undefined && explicitPath !== null) {
    const profile = readProfileFile(String(explicitPath));
    const persona = requestedProfilePlatform(profile, requestedPlatform, "Explicit profile");
    return explicitProfileResult(profile, "explicit-file", options.fingerprint, persona);
  }

  const explicitProfile = options.profile;
  if (isObject(explicitProfile)) {
    const profile = validateProfile(explicitProfile);
    const persona = requestedProfilePlatform(profile, requestedPlatform, "Explicit profile");
    return explicitProfileResult(profile, "explicit-profile", options.fingerprint, persona);
  }
  if (typeof explicitProfile === "string" && looksLikePath(explicitProfile)) {
    const profile = readProfileFile(explicitProfile);
    const persona = requestedProfilePlatform(profile, requestedPlatform, "Explicit profile");
    return explicitProfileResult(profile, "explicit-file", options.fingerprint, persona);
  }

  const requestedId = options.profileId ?? options.profile_id ?? (typeof explicitProfile === "string" ? explicitProfile : undefined);
  if (requestedId !== undefined && requestedId !== null && requestedId !== "") {
    throw new ProfileResolutionError(
      `catalogue profile ids were retired with catalogue version 1; catalogue version ${CATALOGUE_VERSION} composes a profile from anchors and dispersion instead of offering families, so there is no catalogue profile named ${String(requestedId)} to resolve`,
      { requested_profile_id: String(requestedId), model: CATALOGUE_MODEL },
      "APOSTATE_CATALOGUE_PROFILE_IDS_RETIRED",
    );
  }

  const fingerprint = options.fingerprint;
  const hasFingerprint = fingerprint !== undefined && fingerprint !== null && fingerprint !== "";
  if (hasFingerprint) normalizeSeed(fingerprint);
  const persona = requestedPlatform === undefined || requestedPlatform === null || requestedPlatform === ""
    ? null
    : normalizePersona(requestedPlatform);
  if (hasFingerprint && String(fingerprint).trim().toLowerCase() === HOST_INHERITANCE_SEED) {
    if (persona !== null) {
      throw new ProfileResolutionError(HOST_PERSONA_MESSAGE, { fingerprint_platform: persona }, "APOSTATE_COMPOSITION_UNAVAILABLE");
    }
    return {
      profile: null,
      source: "host-inherited",
      profileId: null,
      identity: null,
      catalogueVersion: CATALOGUE_VERSION,
      chromiumVersion: CHROMIUM_VERSION,
      warnings: [],
    };
  }
  throw new ProfileResolutionError(
    COMPOSITION_UNAVAILABLE_MESSAGE,
    {
      fingerprint: hasFingerprint ? fingerprint : null,
      fingerprint_platform: persona,
      catalogue_version: CATALOGUE_VERSION,
      model: CATALOGUE_MODEL,
    },
    "APOSTATE_COMPOSITION_UNAVAILABLE",
  );
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
    if (!["http:", "https:", "socks4:", "socks5:"].includes(parsed.protocol)) {
      throw new TypeError("proxy.server must use http, https, socks4, or socks5.");
    }
    return parsed.toString();
  }
  if (typeof proxy !== "string") throw new TypeError("proxy must be a URL string or an object with server.");
  try {
    const parsed = new URL(proxy);
    if (!parsed.protocol || !parsed.hostname) throw new Error("missing host");
    if (!["http:", "https:", "socks4:", "socks5:"].includes(parsed.protocol)) {
      throw new Error("unsupported scheme");
    }
    return parsed.toString();
  } catch {
    throw new TypeError("proxy must be a valid HTTP(S), SOCKS4, or SOCKS5 URL.");
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

function launchProxyCredentials(proxy) {
  if (!proxy) return null;
  const parsed = new URL(proxy);
  if (!parsed.username && !parsed.password) return null;
  let username;
  let password;
  try {
    username = decodeURIComponent(parsed.username);
    password = decodeURIComponent(parsed.password);
  } catch {
    throw new TypeError("proxy credentials must be valid URL-encoded text.");
  }
  if (username.length > 4096 || password.length > 4096) {
    throw new TypeError("proxy credentials must be at most 4096 characters.");
  }
  return { username, password };
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
  if (options.humanize === true) {
    throw new UnsupportedFeatureError("humanize is not implemented; refusing to accept a no-op launch option.");
  }
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

function proxyAgentForGeoip(target, proxy) {
  if (!proxy) return undefined;
  const proxyUrl = new URL(proxy);
  if (proxyUrl.protocol === "socks4:" || proxyUrl.protocol === "socks5:") {
    return new SocksProxyAgent(proxy);
  }
  if (proxyUrl.protocol !== "http:" && proxyUrl.protocol !== "https:") {
    throw new GeoIPError(`Unsupported GeoIP proxy scheme ${proxyUrl.protocol}.`, {
      proxy: redactProxy(proxy),
    });
  }
  if (proxyUrl.protocol === "https:" || target.protocol === "https:") {
    return new HttpsProxyAgent(proxy);
  }
  return new HttpProxyAgent(proxy);
}

function requestGeoipJson(url, proxy, signal) {
  const target = new URL(url);
  if (target.protocol !== "http:" && target.protocol !== "https:") {
    throw new GeoIPError(`GeoIP URL must use HTTP or HTTPS, got ${target.protocol}.`);
  }
  const requester = target.protocol === "https:" ? httpsRequest : httpRequest;
  const agent = proxyAgentForGeoip(target, proxy);
  return new Promise((resolveResponse, rejectResponse) => {
    let settled = false;
    let request;
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      signal?.removeEventListener("abort", abort);
      callback(value);
    };
    const abort = () => {
      request?.destroy(new Error("GeoIP request aborted."));
    };
    if (signal?.aborted) {
      finish(rejectResponse, Object.assign(new Error("GeoIP request aborted."), { name: "AbortError" }));
      return;
    }
    try {
      request = requester(target, {
        agent,
        headers: {
          accept: "application/json",
          "user-agent": `apostate-node/${PACKAGE_VERSION}`,
        },
        signal,
      }, (response) => {
        let size = 0;
        const chunks = [];
        response.setEncoding("utf8");
        response.on("data", (chunk) => {
          size += Buffer.byteLength(chunk);
          if (size > 1024 * 1024) {
            request.destroy(new Error("GeoIP response exceeded 1 MiB."));
            return;
          }
          chunks.push(chunk);
        });
        response.on("end", () => {
          const body = chunks.join("");
          if (response.statusCode < 200 || response.statusCode >= 300) {
            finish(rejectResponse, new Error(`GeoIP endpoint returned HTTP ${response.statusCode}.`));
            return;
          }
          try {
            finish(resolveResponse, JSON.parse(body));
          } catch {
            finish(rejectResponse, new Error("GeoIP endpoint returned invalid JSON."));
          }
        });
        response.on("error", (error) => finish(rejectResponse, error));
      });
      request.once("error", (error) => {
        if (error?.name === "AbortError" || signal?.aborted) {
          finish(rejectResponse, Object.assign(new Error("GeoIP request aborted."), { name: "AbortError" }));
        } else {
          finish(rejectResponse, error);
        }
      });
      signal?.addEventListener("abort", abort, { once: true });
      request.end();
    } catch (error) {
      finish(rejectResponse, error);
    }
  });
}

async function defaultGeoipLookup(url, proxy, signal) {
  if (url) {
    return requestGeoipJson(url, proxy, signal);
  }

  // Cascading fallbacks for more robust lookups, preferring free, reliable endpoints
  const endpoints = [
    "http://ip-api.com/json/",
    "https://ipapi.co/json/",
    "https://freeipapi.com/api/json"
  ];

  let lastError;
  for (const endpoint of endpoints) {
    try {
      const result = await requestGeoipJson(endpoint, proxy, signal);
      // Ensure API didn't return a custom error state inside 200 OK
      if (result && result.status === 'fail') {
        throw new Error(`API returned fail: ${result.message}`);
      }
      if (result && typeof result === 'object') {
        return result;
      }
    } catch (error) {
      lastError = error;
      // Continue to try the next endpoint in the cascade
    }
  }

  throw lastError || new Error("All default GeoIP endpoints failed.");
}

function validateGeoipResult(result) {
  if (!isObject(result)) throw new GeoIPError("GeoIP resolver must return an object.");

  // Safely parse locale/language from multiple possible API structures
  let locale = result.locale ?? result.language;
  if (!locale) {
    const languages = Array.isArray(result.languages)
      ? result.languages[0]
      : String(result.languages ?? "").split(",")[0] || null;
    locale = languages;
  }

  // Intelligently derive a sensible locale from countryCode if none is explicitly provided
  if (!locale && (result.countryCode || result.country_code)) {
    locale = `en-${(result.countryCode || result.country_code).toUpperCase()}`;
  } else if (!locale) {
    locale = "en-US"; // Practical fallback
  }

  // Find timezone, defaulting to UTC rather than crashing
  const timezone = result.timezone ?? result.time_zone ?? result.timeZone ?? "UTC";
  const ip = result.ip ?? result.query ?? result.ipAddress ?? result.ip_address ?? null;

  if (typeof locale !== "string") throw new GeoIPError("GeoIP locale must be a string.");
  if (typeof timezone !== "string") throw new GeoIPError("GeoIP timezone must be a string.");
  if (ip !== null && (typeof ip !== "string" || isIP(ip) === 0)) throw new GeoIPError("GeoIP resolver returned an invalid IP address.");

  return { locale, timezone, ip };
}

async function prepareLaunch(options = {}) {
  const canonical = toCanonicalLaunchConfig(options);
  const resolution = resolveProfile(options);
  let profile = resolution.profile;
  const inheritedLocale = profileLocale(profile);
  let geoipResult = null;

  if (canonical.geoip && (canonical.locale === null || canonical.timezone === null || (canonical.proxy !== null && !hasSwitch(canonical.args, "--fingerprint-webrtc-ip")))) {
    const controller = new AbortController();
    const timeoutMs = Number.isFinite(options.geoipTimeoutMs) ? Math.max(1, options.geoipTimeoutMs) : 10000;
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const resolver = options.geoipResolver ?? ((context) => defaultGeoipLookup(options.geoipUrl, context.proxy, context.signal));
      geoipResult = validateGeoipResult(await resolver({
        proxy: canonical.proxy,
        proxy_redacted: redactProxy(canonical.proxy),
        signal: controller.signal,
      }));
    } catch (error) {
      // PRACTICAL FIX: Revert to fallback instead of crashing the entire browser launch.
      const reason = error?.name === "AbortError" ? "timed out" : sanitizeErrorMessage(error?.message ?? error, canonical.proxy);
      console.warn(`\x1b[33m[Apostate] GeoIP lookup warning: ${reason}. Defaulting to fallback locale and timezone.\x1b[0m`);
      geoipResult = { locale: "en-US", timezone: "UTC", ip: null };
    } finally {
      clearTimeout(timeout);
    }
  }

  canonical.locale = canonical.locale ?? geoipResult?.locale ?? inheritedLocale.locale ?? null;
  canonical.timezone = canonical.timezone ?? geoipResult?.timezone ?? inheritedLocale.timezone ?? null;
  canonical.webrtc_ip = canonical.proxy !== null && !hasSwitch(canonical.args, "--fingerprint-webrtc-ip")
    ? geoipResult?.ip ?? null
    : null;

  // Hard fallback just in case to ensure we never crash for missing timezone/locale configs
  if (canonical.geoip && (canonical.locale === null || canonical.timezone === null)) {
    canonical.locale = canonical.locale ?? "en-US";
    canonical.timezone = canonical.timezone ?? "UTC";
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
      warnings: resolution.warnings ?? [],
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
  return validateProfile(stripSourceCapture(profile ?? {}));
}
function buildLaunchArguments(config) {
  const args = config.args.filter((arg) => !arg.startsWith("--apostate-profile=") && !arg.startsWith("--proxy-server=") && !arg.startsWith("--user-data-dir="));
  const credentials = launchProxyCredentials(config.proxy);
  const devicePayload = nativeProfilePayload(config.profile);
  if (Object.keys(devicePayload).length > 0 || credentials !== null) {
    const payload = credentials
      ? { device_profile: devicePayload, proxy_credentials: credentials }
      : devicePayload;
    const encoded = Buffer.from(stableStringify(payload), "utf8").toString("base64");
    args.push(`--apostate-profile=${encoded}`);
  }
  if (config.proxy !== null && config.webrtc_ip && !hasSwitch(args, "--fingerprint-webrtc-ip")) {
    args.push(`--fingerprint-webrtc-ip=${config.webrtc_ip}`);
  }
  if (config.proxy !== null && !hasSwitch(args, "--force-webrtc-ip-handling-policy")) {
    args.push("--force-webrtc-ip-handling-policy=disable_non_proxied_udp");
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

export async function verifyArtifact(archive, artifact) {
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
  return { sha256: actualHash };
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
  return new MissingBinaryError(`Apostate Chromium ${CHROMIUM_VERSION} for ${target} is not installed in ${details.cache_dir}.${suffix} Run ensureBinary() with a release manifest or provide executablePath.`, details);
}

async function validCachedBinary(paths, target, artifact) {
  if (!(await isRegularFile(paths.archive)) || !(await isRegularFile(paths.binary)) || !(await isRegularFile(paths.metadata))) return false;
  try {
    const metadata = JSON.parse(await readFile(paths.metadata, "utf8"));
    if (!isObject(metadata) || metadata.package_version !== PACKAGE_VERSION || metadata.chromium_version !== CHROMIUM_VERSION || metadata.catalogue_version !== CATALOGUE_VERSION) return false;
    if (metadata.target !== target || metadata.platform !== target || metadata.artifact !== expectedArtifactName(target)) return false;
    if (typeof artifact?.sha256 !== "string" || !/^[0-9a-f]{64}$/i.test(artifact.sha256)) return false;
    if (metadata.sha256?.toLowerCase() !== artifact.sha256.toLowerCase()) return false;
    const archive = await readFile(paths.archive);
    await verifyArtifact(archive, artifact);
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
    throw new UnpublishedArtifactError(`Apostate binary for ${target} is not published for this release.`, { target });
  }
  if (!options.force && await validCachedBinary(paths, target, artifact)) return paths.binary;
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
  await verifyArtifact(archive, artifact);
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
    cache_dir: resolve(cacheDir),
    binary_path: paths.binary,
    cache_hit: artifact ? await validCachedBinary(paths, target, artifact) : false,
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
  const env = {
    ...(options.env ?? {}),
    ...(prepared.config.timezone ? { TZ: prepared.config.timezone } : {}),
  };
  const child = await spawnBrowser(binary, args, { ...options, env });
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
export const load_catalogue = loadCatalogue;
