#!/usr/bin/env python3
"""Prepare a standalone native/portable NEON experiment from frozen FFT sources.

Only diagnostic work directories are written. Numerical butterfly/planner code
is reused, with native NEON intrinsics replaced by explicit software operations
in the portable variant. No browser or production build is invoked.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def prepare(args):
    baseline = args.baseline.absolute()
    work = args.work.absolute()
    baseline_manifest = (baseline / "source-manifest.json").read_bytes()
    manifest = json.loads(baseline_manifest)
    work.mkdir(parents=True, exist_ok=False)
    for relative, expected in manifest["prepared_files_sha256"].items():
        content = (baseline / relative).read_bytes()
        if sha(content) != expected:
            raise ValueError(f"baseline source changed: {relative}")
        destination = work / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    shutil.copyfile(baseline / "Cargo.lock", work / "Cargo.lock")
    main_path = work / "src/main.rs"
    main = main_path.read_text().replace("fs::read_dir(root)", "fs::read_dir(&root)")
    position = main.rfind("}")
    main = main[:position] + "    complex_controls(&root);\n" + main[position:]
    main += "\n" + (ROOT / "scripts/fixtures/audio-neon-complex-controls.rs").read_text()
    main_path.write_text(main)
    cargo_path = work / "Cargo.toml"
    cargo = cargo_path.read_text()
    cargo += '\n[features]\nportable-neon = []\n'
    if args.portable:
        cargo += 'default = ["portable-neon"]\n'
        cargo = cargo.replace('features = ["avx", "sse", "neon"]',
                              'features = ["avx", "sse", "neon", "portable-neon"]')
        crate = work / "vendor/rustfft-v6"
        library_path = crate / "src/lib.rs"
        library = library_path.read_text()
        old = 'all(target_arch = "aarch64", feature = "neon")'
        if library.count(old) != 2:
            raise ValueError("NEON cfg anchors changed")
        library = library.replace(old, 'all(any(target_arch = "aarch64", feature = "portable-neon"), feature = "neon")')
        library += '\nmod portable_neon;\n'
        library_path.write_text(library)
        for path in (crate / "src/neon").glob("*.rs"):
            source = path.read_text()
            source = source.replace("core::arch::aarch64", "crate::portable_neon")
            source = source.replace("std::arch::aarch64", "crate::portable_neon")
            source = source.replace('#[target_feature(enable = "neon")]', '')
            source = source.replace('std::arch::is_aarch64_feature_detected!("neon")', 'true')
            path.write_text(source)
        shutil.copyfile(ROOT / "scripts/fixtures/audio-portable-neon.rs", crate / "src/portable_neon.rs")
        manifest_path = crate / "Cargo.toml"
        crate_manifest = manifest_path.read_text().replace('[features]\n', '[features]\nportable-neon = []\n')
        manifest_path.write_text(crate_manifest)
        wrapper_path = work / "src/wrapper.rs"
        wrapper = wrapper_path.read_text()
        wrapper = wrapper.replace('use rustfft::{Fft, FftPlanner};', 'use rustfft::{Fft, FftPlannerNeon};')
        wrapper = wrapper.replace('let mut planner = FftPlanner::new();',
                                  'assert!(size.is_power_of_two());\n    let mut planner = FftPlannerNeon::new().unwrap();')
        wrapper_path.write_text(wrapper)
    cargo_path.write_text(cargo)
    files = {str(path.relative_to(work)): sha(path.read_bytes())
             for path in sorted(work.rglob("*")) if path.is_file() and path.name != "Cargo.lock"}
    manifest["baseline_source_manifest_sha256"] = sha(baseline_manifest)
    manifest["experiment"] = "portable-neon-software-lanes" if args.portable else "native-neon-complex-controls"
    manifest["experiment_parameters"] = {
        "lengths": [16,32,64,128,256,512,1024,2048,4096,8192],
        "stimuli": ["impulse","basis","alternating","sequence","heldout","zeros","subnormal"],
        "directions": ["Forward","Inverse"],
        "production_patch": False,
    }
    manifest["prepared_files_sha256"] = files
    (work / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(work)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--portable", action="store_true")
    args = parser.parse_args()
    prepare(args)


if __name__ == "__main__":
    main()
