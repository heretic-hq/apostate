#!/usr/bin/env python3
"""Generate0045 from immutable ANGLE plus earlier series in a private scratch.

No shared Chromium checkout is read or changed. --check-cxx compiles only the
extracted range reducer with type stubs; it is not a Chromium V1/render test.
"""

import argparse
import base64
import concurrent.futures
import difflib
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ANGLE = "7df613367a1d4ca9aea9ece344d4580d32d132a9"
FILES = ["include/platform/PlatformMethods.h", "src/libANGLE/Caps.h",
         "src/libANGLE/Context.cpp", "src/libANGLE/Shader.cpp",
         "src/libANGLE/MemoryProgramCache.cpp", "src/compiler/translator/Compiler.cpp"]


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"expected unique source anchor: {old[:100]!r}")
    return text.replace(old, new, 1)


def edit(path, text):
    if path.endswith("/Caps.h"):
        anchor = "    GLfloat maxAliasedPointSize   = 1.0f;\n"
        return replace_once(text, anchor, anchor +
                            "    bool profilePointSizeClamp   = false;\n")
    if path.endswith("/Context.cpp"):
        anchor = "void ApplyProfileIntegerCaps(Caps *caps)\n{\n"
        text = replace_once(text, anchor, '''void ApplyProfilePointSizeCaps(Caps *caps)
{
    int64_t minimum = 0;
    int64_t maximum = 0;
    const bool hasMinimum = GetProfileIntegerCap("ALIASED_POINT_SIZE_RANGE_MIN", &minimum);
    const bool hasMaximum = GetProfileIntegerCap("ALIASED_POINT_SIZE_RANGE_MAX", &maximum);
    if (!hasMinimum && !hasMaximum)
    {
        return;
    }
    const GLfloat effectiveMin = hasMinimum
        ? std::max(caps->minAliasedPointSize, static_cast<GLfloat>(minimum))
        : caps->minAliasedPointSize;
    const GLfloat effectiveMax = hasMaximum
        ? std::min(caps->maxAliasedPointSize, static_cast<GLfloat>(maximum))
        : caps->maxAliasedPointSize;
    // No overlap cannot be repaired by claiming unsupported point sizes.
    if (effectiveMin > effectiveMax)
    {
        return;
    }
    caps->minAliasedPointSize = effectiveMin;
    caps->maxAliasedPointSize = effectiveMax;
    caps->profilePointSizeClamp = true;
}

void ApplyProfileIntegerCaps(Caps *caps)
{
    ApplyProfilePointSizeCaps(caps);
''')
        anchor = "bool GetPassthroughShaders(egl::Display *display, const egl::AttributeMap &attribs)\n{\n"
        return replace_once(text, anchor, anchor + '''    int64_t pointSize = 0;
    if (GetProfileIntegerCap("ALIASED_POINT_SIZE_RANGE_MIN", &pointSize) ||
        GetProfileIntegerCap("ALIASED_POINT_SIZE_RANGE_MAX", &pointSize))
    {
        // Passthrough bypasses AST transforms and selects SH_NULL_OUTPUT.
        // Disable it before State/Compiler initialization when a real point
        // range constraint needs the translator's ClampPointSize operation.
        return false;
    }
''')
    if path.endswith("/Shader.cpp"):
        anchor = "    // Find a shader in Blob Cache\n"
        return replace_once(text, anchor, '''    // Set this before cache lookup. Shader::setShaderKey hashes the compile
    // options and complete built-in resources, including Min/MaxPointSize.
    if (mState.getShaderType() == ShaderType::Vertex &&
        context->getCaps().profilePointSizeClamp)
    {
        options.clampPointSize = true;
    }

''' + anchor)
    if path.endswith("/MemoryProgramCache.cpp"):
        anchor = "    angle::UpdateHashWithValue(hasher, caps.maxUniformBlockSize);\n"
        return replace_once(text, anchor, anchor + '''    angle::UpdateHashWithValue(hasher, caps.minAliasedPointSize);
    angle::UpdateHashWithValue(hasher, caps.maxAliasedPointSize);
    angle::UpdateHashWithValue(hasher, caps.profilePointSizeClamp);
''')
    if path.endswith("/translator/Compiler.cpp"):
        anchor = '        << ":MaxImageUnits:" << mResources.MaxImageUnits\n'
        return replace_once(text, anchor, '''        << ":MinPointSize:" << mResources.MinPointSize
        << ":MaxPointSize:" << mResources.MaxPointSize
''' + anchor)
    return text


def check_cxx(scratch, sources):
    compiler = shutil.which("clang++") or shutil.which("c++")
    if not compiler:
        raise RuntimeError("--check-cxx requires a C++ compiler")
    (scratch / "PlatformMethods.h").write_text(sources["include/platform/PlatformMethods.h"])
    context = sources["src/libANGLE/Context.cpp"]
    code = context[context.index("bool GetProfileIntegerCap("):context.index("void ApplyProfileIntegerCaps(")]
    harness = '''#include "PlatformMethods.h"
#include <algorithm>
#include <cassert>
#include <map>
#include <string>
using GLfloat = float;
struct Caps { GLfloat minAliasedPointSize=1; GLfloat maxAliasedPointSize=1024;
              bool profilePointSizeClamp=false; };
angle::PlatformMethods methods;
std::map<std::string,int64_t> claims;
angle::PlatformMethods* ANGLEPlatformCurrent() { return &methods; }
bool Read(angle::PlatformMethods*,const char* name,int64_t* value) {
  auto it=claims.find(name); if(it==claims.end()) return false;
  *value=it->second; return true;
}
''' + code + '''
int main() {
  methods.getProfileIntegerCapV1=Read;
  Caps native; ApplyProfilePointSizeCaps(&native);
  assert(native.maxAliasedPointSize==1024 && !native.profilePointSizeClamp);
  claims={{"ALIASED_POINT_SIZE_RANGE_MIN",1},{"ALIASED_POINT_SIZE_RANGE_MAX",511}};
  Caps mac; ApplyProfilePointSizeCaps(&mac);
  assert(mac.minAliasedPointSize==1 && mac.maxAliasedPointSize==511 && mac.profilePointSizeClamp);
  Caps lower; lower.maxAliasedPointSize=255; ApplyProfilePointSizeCaps(&lower);
  assert(lower.maxAliasedPointSize==255);
  claims["ALIASED_POINT_SIZE_RANGE_MIN"]=2;
  Caps restricted; ApplyProfilePointSizeCaps(&restricted);
  assert(restricted.minAliasedPointSize==2 && restricted.maxAliasedPointSize==511);
  claims["ALIASED_POINT_SIZE_RANGE_MIN"]=600;
  Caps disjoint; ApplyProfilePointSizeCaps(&disjoint);
  assert(disjoint.minAliasedPointSize==1 && disjoint.maxAliasedPointSize==1024 && !disjoint.profilePointSizeClamp);
  claims={{"ALIASED_POINT_SIZE_RANGE_MAX",1024}};
  Caps windows; ApplyProfilePointSizeCaps(&windows);
  assert(windows.maxAliasedPointSize==1024 && windows.profilePointSizeClamp);
}
'''
    source = scratch / "point-range-check.cc"
    binary = scratch / "point-range-check"
    source.write_text(harness)
    subprocess.run([compiler, "-std=c++20", str(source), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


HEADER = '''Subject: [PATCH] angle: enforce profile point-size range in real shaders

Intersect native aliased point-size caps with losslessly integral captured
endpoints through the existing0039 callback. Never raise native capability.
Enable the existing ClampPointSize AST transform before shader-cache lookup,
and disable passthrough shaders before State/Compiler creation so they cannot
bypass the transform. No new callback ABI, GL entrypoint or base linkage.

Compiler resources already receive the effective Min/MaxPointSize and shader
keys hash those resources plus compile options. Add the range to resource
strings and program keys as well. Native range below the reference stays red.

References: resources/fingerprints/raw/m4-max-chrome-20260908T163229Z.json [1,511]
and unlabelled-20260910T140818Z.json [1,1024], both WebGL versions.
Ledger proposal: gpu.point-size-conformance. V1 and actual point-pixel boundary
tests remain required; smaller query values alone are not a conformance pass.

'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "patches/0045-angle-point-size-clamp.patch")
    parser.add_argument("--check-cxx", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="apostate-point-patch-") as temporary:
        scratch = Path(temporary)
        def fetch(path):
            url = f"https://chromium.googlesource.com/angle/angle/+/{ANGLE}/{path}?format=TEXT"
            data = base64.b64decode(urllib.request.urlopen(url, timeout=30).read())
            target = scratch / "third_party/angle" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(fetch, FILES))
        subprocess.run(["git", "init", "-q", str(scratch)], check=True)
        includes = ["--include=third_party/angle/" + path for path in FILES]
        for name in (ROOT / "patches/series").read_text().splitlines():
            if not name or name.startswith("#") or name[:4] >= "0045":
                continue
            subprocess.run(["git", "apply", *includes, str(ROOT / "patches" / name)],
                           cwd=scratch, check=True, capture_output=True)
        before = {path: (scratch / "third_party/angle" / path).read_text() for path in FILES}
        after = {path: edit(path, text) for path, text in before.items()}
        diff = [HEADER]
        for path in FILES:
            if before[path] != after[path]:
                full = "third_party/angle/" + path
                diff.append(f"diff --git a/{full} b/{full}\n")
                diff.extend(difflib.unified_diff(before[path].splitlines(True), after[path].splitlines(True),
                                                fromfile="a/" + full, tofile="b/" + full))
        patch = scratch / "0045.patch"
        patch.write_text("".join(diff))
        subprocess.run(["git", "apply", "--check", str(patch)], cwd=scratch, check=True)
        if args.check_cxx:
            check_cxx(scratch, after)
        args.out.write_text(patch.read_text())
    print(f"generated and checked {args.out}")


if __name__ == "__main__":
    main()
