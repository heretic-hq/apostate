#!/usr/bin/env python3
"""Generate the Mac ascent policy patch from pinned source in a scratch tree."""

import argparse
import base64
import difflib
from pathlib import Path
import subprocess
import tempfile
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
REVISION = "79460ebecaa5625e57a5fb679a735659e73dc687"
PATH = "third_party/blink/renderer/platform/fonts/font_metrics.cc"
HEADER = """Subject: [PATCH] fonts: apply the upstream Mac family ascent policy by profile

Chromium adds its existing rounded 15% ascent adjustment to Times, Helvetica
and Courier on macOS. The shared outline policy in 0040 left this branch
behind an IS_MAC build guard, so a Linux Mac profile still used the wrong
font-level ascent and line geometry for those actual faces.

Select the existing producer policy through the profile loader's ua_platform.
An absent platform retains the native build policy; iOS is not treated as
macOS. Family matching uses the selected physical face, not the requested CSS
family name. Preserve the upstream arithmetic and placement after overrides
and rounding. There are no TextMetrics or DOMRect accessor changes.

Reference: resources/fingerprints/raw/m4-max-chrome-20260908T163229Z.json,
fonts.metrics.Times/Helvetica/Courier. The captured 40px Times ascent is 36
and Helvetica ascent is 37; the pre-patch Linux producers return 30 and 31.
Primary source: Chromium 79460ebecaa5625e57a5fb679a735659e73dc687,
third_party/blink/renderer/platform/fonts/font_metrics.cc:135-147.

V1, multi-size font/layout controls and V3 remain required. This does not fix
font fallback selection, CoreText metrics generally, or raster output.

"""


def generate(destination):
    url = f"https://chromium.googlesource.com/chromium/src/+/{REVISION}/{PATH}?format=TEXT"
    with urllib.request.urlopen(url, timeout=45) as response:
        pristine = base64.b64decode(response.read()).decode()
    with tempfile.TemporaryDirectory(prefix="apostate-mac-ascent-") as temporary:
        scratch = Path(temporary)
        source = scratch / PATH
        source.parent.mkdir(parents=True)
        source.write_text(pristine)
        subprocess.run(["git", "apply", f"--include={PATH}",
                        str(ROOT / "patches/0040-font-metric-platform-policy.patch")],
                       cwd=scratch, check=True)
        before = source.read_text()
        after = before.replace('#include "base/compiler_specific.h"',
                               '#include "base/apostate/profile.h"\n#include "base/compiler_specific.h"', 1)
        start = after.index("#if BUILDFLAG(IS_MAC)\n  // We are preserving")
        end = after.index("#endif", start)
        body = after[start:end].split("\n", 1)[1]
        selector = """  const bool use_mac_ascent = [] {
    if (const auto* profile = base::apostate::Profile::Get()) {
      if (const auto& platform = profile->ua_platform()) {
        return *platform == "macOS";
      }
    }
    return static_cast<bool>(BUILDFLAG(IS_MAC));
  }();
  if (use_mac_ascent) {
"""
        after = after[:start] + selector + "".join("  " + line + "\n" for line in body.splitlines()) + "  }" + after[end + len("#endif"):]
        result = HEADER + f"diff --git a/{PATH} b/{PATH}\n" + "".join(
            difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                 fromfile=f"a/{PATH}", tofile=f"b/{PATH}"))
        patch = scratch / "candidate.patch"
        patch.write_text(result)
        subprocess.run(["git", "apply", "--check", str(patch)], cwd=scratch, check=True)
        subprocess.run(["git", "apply", str(patch)], cwd=scratch, check=True)
        subprocess.run(["git", "apply", "--reverse", "--check", str(patch)], cwd=scratch, check=True)
        destination.write_text(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path,
                        default=ROOT / "patches/0046-mac-font-family-ascent.patch")
    args = parser.parse_args()
    generate(args.out)
    print(f"Generated and checked {args.out}")
