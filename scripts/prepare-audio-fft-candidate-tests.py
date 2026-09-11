#!/usr/bin/env python3
"""Prepare standalone tests using actual 0042 candidate source files."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
CRATE = "third_party/rust/chromium_crates_io/vendor/rustfft-v6/"


def sha(data): return hashlib.sha256(data).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", type=Path, required=True)
    ap.add_argument("--candidate", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--portable", action="store_true")
    args = ap.parse_args()
    original = (args.baseline / "source-manifest.json").read_bytes()
    manifest = json.loads(original)
    work = args.work.absolute()
    work.mkdir(parents=True, exist_ok=False)
    for relative, expected in manifest["prepared_files_sha256"].items():
        content = (args.baseline / relative).read_bytes()
        if sha(content) != expected: raise ValueError("baseline changed")
        path = work / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    shutil.copyfile(args.baseline / "Cargo.lock", work / "Cargo.lock")
    candidate_manifest = json.loads((args.candidate / "candidate-manifest.json").read_text())
    for relative, hashes in candidate_manifest["files"].items():
        data = (args.candidate / relative).read_bytes()
        if sha(data) != hashes["after_sha256"]: raise ValueError("candidate source changed")
        if relative.startswith(CRATE + "src/"):
            destination = work / "vendor/rustfft-v6" / relative.removeprefix(CRATE)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
    cargo_path = work / "vendor/rustfft-v6/Cargo.toml"
    cargo_path.write_text(cargo_path.read_text().replace("[features]\n", "[features]\nportable-neon = []\n"))
    cargo_path = work / "Cargo.toml"
    cargo = cargo_path.read_text().replace('features = ["avx", "sse", "neon"]',
                                         'features = ["avx", "sse", "neon", "portable-neon"]')
    cargo += '\n[features]\nportable-neon = []\n'
    if args.portable: cargo += 'default = ["portable-neon"]\n'
    cargo_path.write_text(cargo)

    wrapper = (args.candidate / "third_party/blink/renderer/platform/audio/rustfft_ffi.rs").read_text()
    wrapper = wrapper[:wrapper.index("#[cxx::bridge")]
    anchor = "        fft_inverse.process_with_scratch(&mut self.complex_data, &mut self.scratch);"
    assert wrapper.count(anchor) == 2
    wrapper = wrapper.replace(anchor, '        crate::dump_complex("prepared", &self.complex_data);\n' + anchor +
                              '\n        crate::dump_complex("postfft", &self.complex_data);')
    wrapper += '''
impl RustFft {
    pub fn diagnostic_dump_twiddles(&self) {
        if let FftStrategy::Even { twiddles, .. } = &self.strategy {
            crate::dump_complex("twiddles", twiddles);
        }
    }
}
pub fn diagnostic_cache_check() {
    let native = get_fft(2048, false);
    let portable = get_fft(2048, true);
    assert!(Arc::ptr_eq(&native.0, &get_fft(2048, false).0));
    assert!(Arc::ptr_eq(&portable.0, &get_fft(2048, true).0));
    assert!(!Arc::ptr_eq(&native.0, &portable.0));
}
pub fn diagnostic_plan(size: usize, direction: rustfft::FftDirection,
                       mac_arm_mode: bool) -> Arc<dyn Fft<f32>> {
    let pair = get_fft(size, mac_arm_mode);
    match direction {
        rustfft::FftDirection::Forward => pair.0,
        rustfft::FftDirection::Inverse => pair.1,
    }
}
'''
    (work / "src/wrapper.rs").write_text(wrapper)
    main = (ROOT / "scripts/fixtures/audio-table-main.rs").read_text()
    main = main.replace("fs::read_dir(root)", "fs::read_dir(&root)")
    main = main.replace("wrapper::rustfft_new(size)", 'wrapper::rustfft_new_for_profile(size, cfg!(feature = "portable-neon"))')
    pos = main.rfind("}")
    main = main[:pos] + "    extra_controls(&root);\n" + main[pos:]
    controls = (ROOT / "scripts/fixtures/audio-neon-complex-controls.rs").read_text()
    controls = controls.replace("root: &std::path::Path)", "root: &std::path::Path, label: &str)")
    controls = controls.replace("[16usize, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192]",
                                "[1usize, 2, 3, 4, 5, 7, 8, 9, 12, 15, 16, 17, 25, 30, 32, 63, 64, 128, 256, 257, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536]")
    controls = controls.replace('"zeros", "subnormal"]', '"zeros", "subnormal", "special", "overflow", "payload-heldout"]')
    controls = controls.replace('let mut state = if kind == "heldout"',
                                'let mut state = if kind == "payload-heldout" { 0x92468aceu32 } else if kind == "heldout"')
    controls = controls.replace('"subnormal" =>', '''"special" => Complex::new(
                        if i % 5 == 0 { f32::from_bits(0x7fc12345) } else if i % 7 == 0 { f32::INFINITY } else { -0.25 },
                        if i % 3 == 0 { f32::NEG_INFINITY } else { f32::from_bits(0xffc23456) }),
                    "overflow" => Complex::new(if i % 2 == 0 { f32::MAX } else { -f32::MAX }, f32::MAX / 2.0),
                    "payload-heldout" => {
                        let make_nan = |word: u32| {
                            let payload = (word & 0x003fffff) | 1;
                            f32::from_bits((word & 0x80000000) | 0x7f800000 | payload |
                                           if word & 0x10000000 == 0 {0x00400000}else{0})
                        };
                        Complex::new(if i % 5 == 0 {f32::INFINITY}else{make_nan(next())},
                                     if i % 7 == 0 {0.0}else{make_nan(next())})
                    },
                    "subnormal" =>''')
    controls = controls.replace("rustfft::FftPlannerNeon::<f32>", "rustfft::FftPlannerPortableNeon::<f32>")
    planner_start = controls.index('                #[cfg(feature = "portable-neon")]')
    planner_end = controls.index('                let mut output', planner_start)
    controls = controls[:planner_start] + '''                let fft = wrapper::diagnostic_plan(length, direction,
                    cfg!(feature = "portable-neon"));
''' + controls[planner_end:]
    controls = controls.replace('format!("complex-', 'format!("{label}-complex-')
    main += "\n" + controls + "\n" + (ROOT / "scripts/fixtures/audio-fft-extra-controls.rs").read_text()
    (work / "src/main.rs").write_text(main)
    manifest["baseline_source_manifest_sha256"] = sha(original)
    manifest["candidate_patch_sha256"] = candidate_manifest["candidate_patch_sha256"]
    manifest["experiment"] = "portable-neon-software-lanes" if args.portable else "native-neon-complex-controls"
    manifest["experiment_parameters"] = {"candidate_0042": True, "fp_modes": ["default", "ftz"],
                                         "tiny_large_nonpower_special_controls": True,
                                         "heldout_payload_arrangements": True}
    manifest["prepared_files_sha256"] = {str(path.relative_to(work)): sha(path.read_bytes())
                 for path in sorted(work.rglob("*")) if path.is_file() and path.name != "Cargo.lock"}
    (work / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(work)


if __name__ == "__main__": main()
