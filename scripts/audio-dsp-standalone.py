#!/usr/bin/env python3
"""Build pinned FFT/table stage diagnostics outside the Chromium checkout.

This is a standalone numerical investigation, not V1 or a browser patch. The
PeriodicWave methods and Rust wrapper are extracted from the pinned tree.
Adapters replace allocation/accounting and expose intermediate buffers only.
"""
import argparse
import base64
import concurrent.futures
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REVISION = "79460ebecaa5625e57a5fb679a735659e73dc687"
REMOTE = "https://chromium.googlesource.com/chromium/src/"
VENDOR = "third_party/rust/chromium_crates_io/vendor/"
CRATES = ["rustfft-v6", "num-complex-v0_4", "num-integer-v0_1", "num-traits-v0_2",
          "primal-check-v0_3", "strength_reduce-v0_2", "transpose-v0_2", "autocfg-v1"]
WAVE = "third_party/blink/renderer/modules/webaudio/periodic_wave.cc"
WRAPPER = "third_party/blink/renderer/platform/audio/rustfft_ffi.rs"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def retrieve(url):
    with urllib.request.urlopen(url, timeout=40) as response:
        return response.read()


def method(source, signature):
    start = source.index(signature)
    body = source.index("{", start)
    depth = 1
    end = body + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def normal_manifest(directory, packages):
    """Keep build dependencies/features, omit unused registry/dev dependencies.

    Crate source remains the pinned source. Local paths select the exact
    Chromium vendor versions rather than resolving current registry versions.
    """
    original = tomllib.loads((directory / "Cargo.toml").read_text())
    package = original["package"]
    lines = ["[package]", f'name = "{package["name"]}"', f'version = "{package["version"]}"',
             f'edition = "{package.get("edition", "2015")}"']
    if package.get("build") is False:
        lines.append("build = false")
    elif isinstance(package.get("build"), str):
        lines.append(f'build = "{package["build"]}"')
    dependencies = set()
    for section in ["dependencies", "build-dependencies"]:
        items = original.get(section, {})
        for name, spec in items.items():
            spec = {"version": spec} if isinstance(spec, str) else spec
            if spec.get("optional"):
                continue
            actual = spec.get("package", name)
            if actual not in packages:
                raise ValueError(f"unfrozen dependency: {actual}")
            dependencies.add(name)
            lines += [f"[{section}.{name}]", f'path = "../{packages[actual]}"']
            if "default-features" in spec:
                lines.append("default-features = " + str(spec["default-features"]).lower())
            if spec.get("features"):
                lines.append("features = " + json.dumps(spec["features"]))
            if actual != name:
                lines.append(f'package = "{actual}"')
    features = original.get("features", {})
    if features:
        lines.append("[features]")
        for name, entries in features.items():
            kept = [entry for entry in entries if entry in features or
                    entry.split("/")[0].rstrip("?").removeprefix("dep:") in dependencies]
            lines.append(f"{json.dumps(name)} = {json.dumps(kept)}")
    (directory / "Cargo.toml").write_text("\n".join(lines) + "\n")


def prepare(work):
    work.mkdir(parents=True, exist_ok=False)
    source_records = {}

    def archive(name):
        url = f"{REMOTE}+archive/{REVISION}/{VENDOR}{name}.tar.gz"
        return name, url, retrieve(url)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for name, url, data in pool.map(archive, CRATES):
            destination = work / "vendor" / name
            destination.mkdir(parents=True)
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive_file:
                archive_file.extractall(destination, filter="data")
            source_records[name] = {"url": url, "archive_sha256": digest(data)}
    packages = {}
    for name in CRATES:
        package = tomllib.loads((work / "vendor" / name / "Cargo.toml").read_text())["package"]
        packages[package["name"]] = name
        source_records[name]["version"] = package["version"]
    for name in CRATES:
        normal_manifest(work / "vendor" / name, packages)

    def source(path):
        url = f"{REMOTE}+/{REVISION}/{path}?format=TEXT"
        data = base64.b64decode(retrieve(url))
        source_records[path] = {"url": url, "sha256": digest(data)}
        return data.decode()

    wave = source(WAVE)
    wrapper = source(WRAPPER)
    (work / "pristine_periodic_wave.cc").write_text(wave)
    (work / "pristine_rustfft_ffi.rs").write_text(wrapper)
    methods = "\n\n".join(method(wave, signature) for signature in [
        "PeriodicWaveImpl::PeriodicWaveImpl(float sample_rate)",
        "unsigned PeriodicWaveImpl::PeriodicWaveSize() const",
        "unsigned PeriodicWaveImpl::MaxNumberOfPartials() const",
        "unsigned PeriodicWaveImpl::NumberOfPartialsForRange(",
        "bool PeriodicWaveImpl::CreateBandLimitedTables(",
        "bool PeriodicWaveImpl::GenerateBasicWaveform(int shape)",
    ])
    anchor = "    vector_math::Vsmul(data_span, normalization_scale, data_span, fft_size);"
    if methods.count(anchor) != 1:
        raise ValueError("normalization source anchor changed")
    methods = methods.replace(anchor, anchor + """
    if (finalize_tables) {
      WriteFloats(case_directory / (RangePrefix(current_range) + ".normalized.f32"), data_span);
      WriteFloats(case_directory / (RangePrefix(current_range) + ".normalizer.f32"),
                  base::span<const float>(&normalization_scale, 1));
    }
""")
    cpp = (ROOT / "scripts/fixtures/audio-table-driver.cc").read_text()
    cpp = cpp.replace("// GENERATED_PERIODIC_WAVE_METHODS", methods)
    (work / "table_driver.cc").write_text(cpp)

    # Keep the exact numerical wrapper. Only remove its Chromium CXX bindings
    # and add stage dumps around the existing inverse-transform operation.
    wrapper = wrapper[:wrapper.index("#[cxx::bridge")]
    anchor = "        fft_inverse.process_with_scratch(&mut self.complex_data, &mut self.scratch);"
    if wrapper.count(anchor) != 2:
        raise ValueError("inverse FFT source anchors changed")
    wrapper = wrapper.replace(anchor,
        '        crate::dump_complex("prepared", &self.complex_data);\n' + anchor +
        '\n        crate::dump_complex("postfft", &self.complex_data);')
    wrapper += """
impl RustFft {
    pub fn diagnostic_dump_twiddles(&self) {
        if let FftStrategy::Even { twiddles, .. } = &self.strategy {
            crate::dump_complex("twiddles", twiddles);
        }
    }
}
"""
    (work / "src").mkdir()
    (work / "src/wrapper.rs").write_text(wrapper)
    shutil.copyfile(ROOT / "scripts/fixtures/audio-table-main.rs", work / "src/main.rs")
    # Log the selected standalone planner, without changing its arithmetic.
    planner_path = work / "vendor/rustfft-v6/src/plan.rs"
    planner = planner_path.read_text()
    for variable, kind in [("avx", "Avx"), ("sse", "Sse"), ("neon", "Neon")]:
        anchor = f"if let Ok({variable}_planner) = FftPlanner{kind}::new() {{"
        if planner.count(anchor) != 1:
            raise ValueError("planner source anchor changed")
        planner = planner.replace(anchor, anchor + f'\n            eprintln!("diagnostic-planner:{kind}");')
    planner_path.write_text(planner)
    (work / "Cargo.toml").write_text('''[package]
name = "apostate-audio-stages"
version = "0.0.0"
edition = "2021"
[dependencies]
rustfft = { path = "vendor/rustfft-v6", features = ["avx", "sse", "neon"] }
[profile.release]
opt-level = 2
codegen-units = 1
''')
    files = {str(path.relative_to(work)): digest(path.read_bytes())
             for path in sorted(work.rglob("*")) if path.is_file()}
    (work / "source-manifest.json").write_text(json.dumps({
        "diagnostic_only": True, "chromium_revision": REVISION, "sources": source_records,
        "prepared_files_sha256": files,
        "adapters": ["pinned numerical methods extracted", "CXX bridge removed",
                     "allocation/accounting stubs", "planner/stage logging",
                     "Mac vDSP scalar multiply/max; other hosts equivalent scalar finite-float operations",
                     "Cargo manifests use frozen local dependency paths; unused optional/dev deps omitted"],
    }, indent=2) + "\n")


def run(command, work, log, env=None):
    with log.open("w") as stream:
        subprocess.run(command, cwd=work, env=env, stdout=stream, stderr=subprocess.STDOUT, check=True)


def build_run(args):
    work = args.work.absolute()
    manifest = json.loads((work / "source-manifest.json").read_text())
    for relative, expected in manifest["prepared_files_sha256"].items():
        if digest((work / relative).read_bytes()) != expected:
            raise ValueError(f"prepared source changed: {relative}")
    cargo = shutil.which(args.cargo) or args.cargo
    rustc = shutil.which(args.rustc) or args.rustc
    cxx = shutil.which(args.cxx) or args.cxx
    env = os.environ.copy()
    env["RUSTC"] = str(rustc)
    stages = work / "stages"
    if not args.compile_only:
        stages.mkdir(exist_ok=False)
    commands = [[cargo, "build", "--offline", "--release", "--jobs", "2"],
                [cxx, "-std=c++20", "-O2", "-ffp-contract=off", "table_driver.cc", "-o", "table_driver"]]
    if sys.platform == "darwin":
        commands[1] += ["-framework", "Accelerate"]
    receipt = {"diagnostic_only": True, "not_v1": True, "platform": platform.platform(),
               "machine": platform.machine(), "commands": commands,
               "rustc": subprocess.check_output([rustc, "-vV"], text=True),
               "cargo": subprocess.check_output([cargo, "--version"], text=True),
               "cxx": subprocess.check_output([cxx, "--version"], text=True),
               "source_manifest_sha256": digest((work / "source-manifest.json").read_bytes())}
    try:
        binaries = ["table_driver", "target/release/apostate-audio-stages"]
        if args.execute_only:
            compiled = json.loads((work / "compilation-receipt.json").read_text())
            if compiled["source_manifest_sha256"] != receipt["source_manifest_sha256"]:
                raise ValueError("compiled source manifest differs")
            for name in binaries:
                if digest((work / name).read_bytes()) != compiled["binaries_sha256"][name]:
                    raise ValueError("compiled binary changed")
        else:
            run(commands[0], work, work / "cargo.log", env)
            run(commands[1], work, work / "cxx.log")
        receipt["binaries_sha256"] = {name: digest((work / name).read_bytes()) for name in binaries}
        if args.compile_only:
            receipt["status"] = "compiled"
            (work / "compilation-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
            return
        run([str(work / "table_driver"), "prepare", str(stages)], work, work / "prepare.log")
        run([str(work / "target/release/apostate-audio-stages"), str(stages)], work, work / "fft.log")
        run([str(work / "table_driver"), "finalize", str(stages)], work, work / "finalize.log")
        receipt["status"] = "completed"
        receipt["stage_files"] = {str(path.relative_to(stages)): {
            "sha256": digest(path.read_bytes()), "bytes": path.stat().st_size}
            for path in sorted(stages.rglob("*.f32"))}
        receipt["cargo_lock_sha256"] = digest((work / "Cargo.lock").read_bytes())
    except Exception as error:
        receipt["status"] = "failed"
        receipt["error"] = str(error)
        raise
    finally:
        (work / "run-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="fresh directory for --prepare")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--run", action="store_true")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--compile-only", action="store_true", help="compile and freeze binaries for inspection")
    group.add_argument("--execute-only", action="store_true", help="execute previously frozen binaries without rebuilding")
    parser.add_argument("--cargo", default="cargo")
    parser.add_argument("--rustc", default="rustc")
    parser.add_argument("--cxx", default="clang++")
    args = parser.parse_args()
    if not (args.prepare or args.run):
        parser.error("select --prepare and/or --run")
    if args.prepare:
        prepare(args.work.absolute())
    if args.run:
        build_run(args)
    print(args.work)


if __name__ == "__main__":
    main()
