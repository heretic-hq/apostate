#!/usr/bin/env python3
"""Generate candidate 0042 from immutable .83 source, without using .workspace."""
import argparse
import base64
import concurrent.futures
import difflib
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REV = "79460ebecaa5625e57a5fb679a735659e73dc687"
CRATE = "third_party/rust/chromium_crates_io/vendor/rustfft-v6/"
NEON = ["mod.rs", "neon_common.rs", "neon_utils.rs", "neon_vector.rs",
        "neon_butterflies.rs", "neon_prime_butterflies.rs", "neon_planner.rs", "neon_radix4.rs"]
PATHS = ["third_party/blink/renderer/platform/audio/fft_frame.cc",
         "third_party/blink/renderer/platform/audio/rustfft_ffi.rs",
         "third_party/rust/rustfft/v6/BUILD.gn", CRATE + "Cargo.toml", CRATE + "src/lib.rs"] + [
             CRATE + "src/neon/" + name for name in NEON]


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"source anchor not unique: {old[:80]!r}")
    return text.replace(old, new, 1)


KERNEL_MACROS = """// Preserve the original inlining policy for native and software kernels.
macro_rules! fft_kernel { ($body:item) => { #[inline(always)] $body }; }
macro_rules! fft_kernel_noinline { ($body:item) => { $body }; }

"""
FMA_KERNEL_MACROS = """// The actual arithmetic body, not an outer closure, owns the FMA feature.
// Construction is guarded by the runtime FMA capability check below.
macro_rules! fft_kernel { ($body:item) => { #[target_feature(enable = "fma")] #[inline] $body }; }
macro_rules! fft_kernel_noinline { ($body:item) => { #[target_feature(enable = "fma")] #[inline] $body }; }

"""


def wrap_kernels(text):
    matches = list(re.finditer(r"(?m)^([ \t]*)(?:pub(?:\(crate\))?\s+)?unsafe fn (?:perform_[A-Za-z0-9_]+|butterfly_4)\b", text))
    for match in reversed(matches):
        start = match.start()
        opening = text.index("{", match.end())
        end, depth = opening + 1, 1
        while depth:
            depth += (text[end] == "{") - (text[end] == "}")
            end += 1
        indent = match[1]
        attribute = indent + "#[inline(always)]\n"
        inline = text[:start].endswith(attribute)
        item = text[start:end]
        if inline:
            start -= len(attribute)
        macro = "fft_kernel" if inline else "fft_kernel_noinline"
        text = text[:start] + indent + macro + "! {\n" + item + "\n" + indent + "}" + text[end:]
    return text


def modifications(original):
    result = {}
    for path, raw in original.items():
        text = raw.replace("\r\n", "\n")
        if path.endswith("fft_frame.cc"):
            text = once(text, '#include "base/check_op.h"',
                        '#include "base/apostate/profile.h"\n#include "base/check_op.h"')
            text = once(text, "    rust_fft_ = blink::rust_fft::rustfft_new(fft_size_);", """    const auto* profile = base::apostate::Profile::Get();
    const bool mac_arm_mode = profile && profile->ua_platform() == "macOS" &&
                              profile->ua_architecture() == "arm";
    rust_fft_ = blink::rust_fft::rustfft_new_for_profile(fft_size_, mac_arm_mode);""")
        elif path.endswith("rustfft_ffi.rs"):
            text = once(text, "use rustfft::{Fft, FftPlanner};",
                        "use rustfft::{Fft, FftPlanner, FftPlannerPortableNeon};")
            text = text.replace("HashMap<usize, FftPair>", "HashMap<(usize, bool), FftPair>")
            text = once(text, "fn get_fft(size: usize) -> FftPair {",
                        "fn get_fft(size: usize, mac_arm_mode: bool) -> FftPair {")
            text = once(text, "    if let Some(pair) = cache.read().unwrap().get(&size) {",
                        "    let key = (size, mac_arm_mode);\n    if let Some(pair) = cache.read().unwrap().get(&key) {")
            text = once(text, """    let mut planner = FftPlanner::new();
    let forward = planner.plan_fft_forward(size);
    let inverse = planner.plan_fft_inverse(size);
    let pair = (forward, inverse);""", """    let pair = if mac_arm_mode {
        plan_mac_arm(size)
    } else {
        let mut planner = FftPlanner::new();
        (planner.plan_fft_forward(size), planner.plan_fft_inverse(size))
    };""")
            text = once(text, "/// Strategy for computing the FFT based on the input size.", """// The target arithmetic is constant; only its instruction implementation is
// selected by host capability. No native-planner fallback changes the result.
fn plan_mac_arm(size: usize) -> FftPair {
    #[cfg(target_arch = "aarch64")]
    if let Ok(mut planner) = rustfft::FftPlannerNeon::<f32>::new() {
        return (planner.plan_fft_forward(size), planner.plan_fft_inverse(size));
    }
    #[cfg(target_arch = "x86_64")]
    if let Ok(mut planner) = rustfft::FftPlannerPortableNeonFma::<f32>::new() {
        return (planner.plan_fft_forward(size), planner.plan_fft_inverse(size));
    }
    let mut planner = FftPlannerPortableNeon::<f32>::new()
        .expect("portable f32 planner is always available");
    (planner.plan_fft_forward(size), planner.plan_fft_inverse(size))
}

/// Strategy for computing the FFT based on the input size.""")
            text = once(text, "cache_write.entry(size).or_insert(pair).clone()",
                        "cache_write.entry(key).or_insert(pair).clone()")
            text = once(text, "pub fn rustfft_new(size: usize) -> Box<RustFft> {", """pub fn rustfft_new(size: usize) -> Box<RustFft> {
    rustfft_new_for_profile(size, false)
}

/// Constructs an FFT with an immutable, embedder-selected arithmetic mode.
/// Both modes use the existing size contract; neither substitutes captured data.
pub fn rustfft_new_for_profile(size: usize, mac_arm_mode: bool) -> Box<RustFft> {""")
            text = text.replace("get_fft(half_size)", "get_fft(half_size, mac_arm_mode)")
            text = text.replace("get_fft(size)", "get_fft(size, mac_arm_mode)")
            text = once(text, "        fn rustfft_new(size: usize) -> Box<RustFft>;",
                        "        fn rustfft_new(size: usize) -> Box<RustFft>;\n"
                        "        fn rustfft_new_for_profile(size: usize, mac_arm_mode: bool) -> Box<RustFft>;")
            text = text.replace("mapping FFT size to its forward/inverse plan", "mapping FFT size and arithmetic mode to its forward/inverse plan")
        elif path.endswith("BUILD.gn"):
            text = once(text, "  sources = [\n", "  sources = [\n"
                        f'    "//{CRATE}src/portable_neon/mod.rs",\n'
                        f'    "//{CRATE}src/portable_neon/intrinsics.rs",\n'
                        f'    "//{CRATE}src/portable_neon/intrinsics_x86.rs",\n'
                        f'    "//{CRATE}src/portable_neon/nan.rs",\n'
                        f'    "//{CRATE}src/portable_neon_fma/mod.rs",\n')
            text = once(text, '  features = [\n', '  features = [\n    "portable-neon",\n')
        elif path.endswith("Cargo.toml"):
            text = once(text, "[features]\n", "[features]\nportable-neon = []\n")
        elif path.endswith("src/lib.rs"):
            text += """
// Independent arithmetic provider; the default planner's CPU selection is unchanged.
#[cfg(feature = "portable-neon")]
mod portable_neon;
#[cfg(feature = "portable-neon")]
pub use self::portable_neon::neon_planner::FftPlannerNeon as FftPlannerPortableNeon;
#[cfg(all(feature = "portable-neon", target_arch = "x86_64"))]
mod portable_neon_fma;
#[cfg(all(feature = "portable-neon", target_arch = "x86_64"))]
pub use self::portable_neon_fma::neon_planner::FftPlannerNeon as FftPlannerPortableNeonFma;
"""
        elif path.endswith("src/neon/mod.rs"):
            text = once(text, "use std::arch::aarch64::{float32x4_t, float64x2_t};", """use core::arch::aarch64 as intrinsics;
use self::intrinsics::{float32x4_t, float64x2_t};

fn neon_available() -> bool {
    std::arch::is_aarch64_feature_detected!("neon")
}""")
            text = KERNEL_MACROS + text
        elif "/src/neon/" in path:
            text = text.replace("use core::arch::aarch64", "use super::intrinsics")
            text = text.replace("use std::arch::aarch64", "use super::intrinsics")
            text = text.replace("crate::neon::", "super::")
            text = text.replace('std::arch::is_aarch64_feature_detected!("neon")', "super::neon_available()")
            text = text.replace('#[target_feature(enable = "neon")]',
                                '#[cfg_attr(target_arch = "aarch64", target_feature(enable = "neon"))]')
            if path.endswith(("neon_butterflies.rs", "neon_prime_butterflies.rs", "neon_radix4.rs")):
                text = wrap_kernels(text)
        result[path] = text.replace("\n", "\r\n") if "\r\n" in raw else text

    # Compile the same numeric modules under a different intrinsic provider.
    portable = original[CRATE + "src/neon/mod.rs"].replace("\r\n", "\n")
    for name in ["neon_common", "neon_vector", "neon_butterflies", "neon_prime_butterflies",
                 "neon_radix4", "neon_utils", "neon_planner"]:
        marker = ("pub mod " if f"pub mod {name};" in portable else "mod ") + name + ";"
        portable = once(portable, marker, f'#[path = "../neon/{name}.rs"]\n' + marker)
    portable = once(portable, "use std::arch::aarch64::{float32x4_t, float64x2_t};", """#[cfg(not(target_arch = "x86_64"))]
mod intrinsics;
#[cfg(target_arch = "x86_64")]
#[path = "intrinsics_x86.rs"]
mod intrinsics;
use self::intrinsics::{float32x4_t, float64x2_t};
mod nan;

fn neon_available() -> bool { true }
const HARDWARE_FMA: bool = false;""")
    result[CRATE + "src/portable_neon/mod.rs"] = KERNEL_MACROS + portable
    fma = portable
    provider_start = fma.index('#[cfg(not(target_arch = "x86_64"))]')
    provider_end = fma.index("use self::intrinsics", provider_start)
    fma = fma[:provider_start] + '#[path = "../portable_neon/intrinsics_x86.rs"]\nmod intrinsics;\n' + fma[provider_end:]
    fma = fma.replace("fn neon_available() -> bool { true }",
                      'fn neon_available() -> bool { std::arch::is_x86_feature_detected!("fma") }')
    fma = fma.replace("const HARDWARE_FMA: bool = false;", "const HARDWARE_FMA: bool = true;")
    fma = fma.replace("mod nan;", '#[path = "../portable_neon/nan.rs"]\nmod nan;')
    result[CRATE + "src/portable_neon_fma/mod.rs"] = FMA_KERNEL_MACROS + fma
    intrinsics = (ROOT / "scripts/fixtures/audio-portable-neon.rs").read_text()
    intrinsics = intrinsics[intrinsics.index("#![allow"):]
    result[CRATE + "src/portable_neon/intrinsics.rs"] = """// Software lane operations for the shared NEON FFT arithmetic.
// Fused operations deliberately use one IEEE rounding step.
""" + intrinsics
    result[CRATE + "src/portable_neon/intrinsics_x86.rs"] = (
        ROOT / "scripts/fixtures/audio-portable-neon-x86.rs").read_text()
    result[CRATE + "src/portable_neon/nan.rs"] = (
        ROOT / "scripts/fixtures/audio-arm-nan-policy.rs").read_text()
    return result


HEADER = """Subject: [PATCH] audio: select portable Mac ARM FFT arithmetic from the profile

The Mac ARM reference and x86 native RustFFT planners produce different wave
tables from identical prepared FFT inputs. Preserve the reference planner's
algorithm using a separate software lane provider; leave all native planners
and their CPU availability checks unchanged.

FFTFrame reads the existing platform profile and passes an internal mode to
the Rust factory. Cache plans by size and mode so a native frame cannot supply
a cached plan to a Mac ARM frame. No captured samples or hashes enter the DSP.

Reference: resources/fingerprints/raw/m4-max-chrome-20260908T163229Z.json
Ledger: audio.dsp-conformance, open. Browser verification and independent
compressor arithmetic differences remain required before conformance closure.

"""


def generate(scratch, out):
    # A scratch directory may be beneath the main repository's ignored tree.
    # Give git apply its own root so it cannot silently filter every hunk as
    # outside the caller's subdirectory.
    subprocess.run(["git", "init", "--quiet"], cwd=scratch, check=True)
    def fetch(path):
        url = f"https://chromium.googlesource.com/chromium/src/+/{REV}/{path}?format=TEXT"
        with urllib.request.urlopen(url, timeout=40) as response:
            return path, base64.b64decode(response.read()).decode()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        original = dict(pool.map(fetch, PATHS))
    for path, text in original.items():
        target = scratch / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, newline="")
    selected = [f"--include={path}" for path in PATHS]
    for patch in (ROOT / "patches/series").read_text().splitlines():
        if not patch or patch.startswith("#"): continue
        if int(patch[:4]) > 41: break
        subprocess.run(["git", "apply", *selected, str(ROOT / "patches" / patch)],
                       cwd=scratch, check=True, capture_output=True)
    original = {path: (scratch / path).read_bytes().decode() for path in PATHS}
    modified = modifications(original)
    diff = [HEADER]
    for path, after in modified.items():
        before = original.get(path, "")
        if before == after: continue
        diff.append(f"diff --git a/{path} b/{path}\n")
        if path not in original:
            diff.append("new file mode 100644\n")
        diff.extend(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                    fromfile=f"a/{path}" if path in original else "/dev/null", tofile=f"b/{path}"))
    patch_path = scratch / "0042.patch"
    patch_path.write_text("".join(diff), newline="")
    subprocess.run(["git", "apply", "--check", str(patch_path)], cwd=scratch, check=True, capture_output=True)
    subprocess.run(["git", "apply", str(patch_path)], cwd=scratch, check=True, capture_output=True)
    for path, after in modified.items():
        if (scratch / path).read_bytes() != after.encode():
            raise ValueError(f"applied candidate differs from generated source: {path}")
    out.write_bytes(patch_path.read_bytes())
    (scratch / "candidate-manifest.json").write_text(json.dumps({
        "chromium_revision": REV, "candidate_patch_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "files": {path: {"before_sha256": hashlib.sha256(original.get(path, "").encode()).hexdigest(),
                         "after_sha256": hashlib.sha256(after.encode()).hexdigest()}
                  for path, after in modified.items()},
    }, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "patches/0042-audio-portable-neon-profile.patch")
    parser.add_argument("--scratch", type=Path)
    args = parser.parse_args()
    if args.scratch:
        args.scratch.mkdir(parents=True, exist_ok=False)
        generate(args.scratch.absolute(), args.out.absolute())
    else:
        with tempfile.TemporaryDirectory(prefix="apostate-audio-0042-") as tmp:
            generate(Path(tmp), args.out.absolute())
    print(args.out)


if __name__ == "__main__":
    main()
