#!/usr/bin/env node
// `npx apostate` -- install and run the browser from a terminal.
//
// Four verbs, because they are the four things a terminal is for here: get the
// browser, find out where it is, run it, and throw it away.
import { spawn } from "node:child_process";
import { binaryInfo, clearCache, ensureBinary, CHROMIUM_VERSION, PACKAGE_VERSION } from "../dist/index.js";

const USAGE = `apostate ${PACKAGE_VERSION} (Chromium ${CHROMIUM_VERSION})

  apostate install [--force] [--keep-archive]   download, verify and extract the browser
  apostate path                                 print the executable path, installing if needed
  apostate info                                 print install and manifest state as JSON
  apostate clear                                delete the install cache
  apostate run [-- <browser args>]              run the browser, forwarding arguments

  --cache-dir <dir>   override the install cache directory
  --manifest <src>    release manifest path, URL, or JSON file
  --target <target>   platform target; defaults to this host
`;

const argv = process.argv.slice(2);
const options = {};
const rest = [];
for (let index = 0; index < argv.length; index += 1) {
  const item = argv[index];
  if (item === "--") { rest.push(...argv.slice(index + 1)); break; }
  if (item === "--cache-dir") { options.cacheDir = argv[++index]; continue; }
  if (item === "--manifest") { options.manifest = argv[++index]; continue; }
  if (item === "--target") { options.target = argv[++index]; continue; }
  if (item === "--force") { options.force = true; continue; }
  if (item === "--keep-archive") { process.env.APOSTATE_KEEP_ARCHIVE = "1"; continue; }
  if (item === "--version" || item === "-v") { console.log(`apostate ${PACKAGE_VERSION} (Chromium ${CHROMIUM_VERSION})`); process.exit(0); }
  if (item === "--help" || item === "-h") { console.log(USAGE); process.exit(0); }
  rest.push(item);
}

const command = rest.shift();
try {
  if (command === "clear") {
    await clearCache(options);
  } else if (command === "info") {
    console.log(JSON.stringify(await binaryInfo(options), null, 2));
  } else if (command === "install" || command === "path") {
    console.log(await ensureBinary(options));
  } else if (command === "run") {
    const executable = await ensureBinary(options);
    // The browser owns the terminal and the exit code from here on.
    const child = spawn(executable, rest, { stdio: "inherit" });
    child.on("exit", (code, signal) => process.exit(signal ? 1 : code ?? 0));
  } else {
    console.log(USAGE);
    process.exit(command === undefined ? 1 : 2);
  }
} catch (error) {
  console.error(`apostate: ${error?.message ?? error}`);
  process.exit(1);
}
