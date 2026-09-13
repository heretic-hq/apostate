import { copyFile, mkdir, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const source = join(root, "..", "src", "index.ts");
const output = join(root, "..", "dist", "index.js");
const declarations = join(root, "..", "dist", "index.d.ts");
await mkdir(dirname(output), { recursive: true });
await copyFile(source, output);
await writeFile(declarations, `export declare const PACKAGE_VERSION: string;
export declare const CHROMIUM_VERSION: string;
export declare const CATALOGUE_VERSION: number;

export interface LaunchOptions {
  fingerprint?: string | number | null;
  fingerprintPlatform?: string;
  fingerprint_platform?: string;
  profile?: string | Record<string, unknown>;
  profilePath?: string;
  profile_file?: string;
  profileId?: string;
  profile_id?: string;
  locale?: string;
  timezone?: string;
  geoip?: boolean;
  proxy?: string | { server: string; username?: string; password?: string };
  headless?: boolean;
  userDataDir?: string;
  user_data_dir?: string;
  args?: string[];
  executablePath?: string;
  binaryPath?: string;
  target?: string;
  [key: string]: unknown;
}

export interface CanonicalLaunchConfig {
  fingerprint: string | number | null;
  fingerprint_platform: string | null;
  profile: Record<string, unknown> | null;
  locale: string | null;
  timezone: string | null;
  geoip: boolean;
  proxy: string | null;
  headless: boolean;
  user_data_dir: string | null;
  args: string[];
}

export class ApostateError extends Error { code: string; details: Record<string, unknown>; }
export class UnsupportedPlatformError extends ApostateError {}
export class ProfileResolutionError extends ApostateError {}
export class ManifestError extends ApostateError {}
export declare class UnpublishedArtifactError extends ManifestError {}
export class MissingBinaryError extends ApostateError {}
export class BinaryIntegrityError extends ApostateError {}
export class BinarySignatureError extends ApostateError {}
export class BinaryDownloadError extends ApostateError {}
export class BinaryExtractionError extends ApostateError {}
export class GeoIPError extends ApostateError {}
export class BrowserLaunchError extends ApostateError {}
export class UnsupportedFeatureError extends ApostateError {}

export declare function stableStringify(value: unknown): string;
export declare function validateProfile(profile: Record<string, unknown>): Record<string, unknown>;
export declare function normalizePersona(value?: string | null): string | null;
export declare function targetForHost(platform?: string, architecture?: string): string;
export declare function normalizeTarget(target?: string | null): string;
export declare function resolveProfile(options?: LaunchOptions): Record<string, unknown>;
export declare function redactProxy(proxy?: LaunchOptions["proxy"]): string | null;
export declare function toCanonicalLaunchConfig(options?: LaunchOptions): CanonicalLaunchConfig;
export declare function resolveLaunchConfig(options?: LaunchOptions): Promise<CanonicalLaunchConfig>;
export declare function canonicalManifestBytes(value: unknown): Uint8Array;
export declare function verifyArtifact(archive: unknown, manifest: Record<string, unknown>, artifact: Record<string, unknown>, options?: Record<string, unknown>): Promise<{ sha256: string; signature_verified: boolean }>;
export declare function ensureBinary(options?: LaunchOptions | string): Promise<string>;
export declare function binaryInfo(options?: LaunchOptions): Promise<Record<string, unknown>>;
export declare function clearCache(options?: LaunchOptions | string): Promise<void>;
export declare function launch(options?: LaunchOptions): Promise<ApostateBrowser>;
export declare function launchContext(options?: LaunchOptions): Promise<ApostateBrowserContext>;
export declare function launchPersistentContext(userDataDir: string, options?: LaunchOptions): Promise<ApostateBrowserContext>;
export declare const launch_context: typeof launchContext;
export declare const launch_persistent_context: typeof launchPersistentContext;
export declare const ensure_binary: typeof ensureBinary;
export declare const binary_info: typeof binaryInfo;
export declare const clear_cache: typeof clearCache;
export declare const translateOptions: typeof toCanonicalLaunchConfig;

export class ApostateBrowser {
  readonly process: unknown;
  readonly executablePath: string;
  readonly launchConfig: CanonicalLaunchConfig;
  readonly diagnostics: Record<string, unknown>;
  isConnected(): boolean;
  contexts(): ApostateBrowserContext[];
  newContext(options?: Record<string, unknown>): Promise<ApostateBrowserContext>;
  newPage(): Promise<never>;
  close(): Promise<void>;
}
export class ApostateBrowserContext {
  readonly browser: ApostateBrowser;
  readonly launchConfig: CanonicalLaunchConfig;
  readonly persistent: boolean;
  isClosed(): boolean;
  newPage(): Promise<never>;
  close(): Promise<void>;
}
`, "utf8");

// Keep package-local tests deterministic when a previous build left stale files.
await rm(join(root, "..", "dist", ".tsbuildinfo"), { force: true });
