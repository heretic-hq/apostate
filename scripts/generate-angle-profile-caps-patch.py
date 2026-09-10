#!/usr/bin/env python3
"""Generate patch 0039 from pinned source in a temporary checkout.

Only the selected files are fetched. Existing patches through 0038 are applied
to those files before generating the next diff. The shared checkout is unused.
"""

import argparse
import base64
import concurrent.futures
import difflib
import pathlib
import shutil
import subprocess
import tempfile
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parents[1]
CHROMIUM = "79460ebecaa5625e57a5fb679a735659e73dc687"
ANGLE = "7df613367a1d4ca9aea9ece344d4580d32d132a9"
FILES = {
    "ui/gl/angle_platform_impl.cc": ("chromium/src", CHROMIUM, "ui/gl/angle_platform_impl.cc"),
    "third_party/angle/include/platform/PlatformMethods.h":
        ("angle/angle", ANGLE, "include/platform/PlatformMethods.h"),
    "third_party/angle/src/libANGLE/Context.cpp":
        ("angle/angle", ANGLE, "src/libANGLE/Context.cpp"),
    "third_party/angle/src/libANGLE/MemoryProgramCache.cpp":
        ("angle/angle", ANGLE, "src/libANGLE/MemoryProgramCache.cpp"),
}


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f"expected one source anchor: {old[:80]!r}")
    return source.replace(old, new, 1)


def edit(path, source):
    if path.endswith("PlatformMethods.h"):
        source = replace_once(source, "// Platform methods are enumerated here once.\n", """// Immutable embedder profile input. The callback copies one integer; ANGLE does
// not own or parse the profile. False leaves the supplied output untouched.
using GetProfileIntegerCapV1Func = bool (*)(PlatformMethods *platform,
                                          const char *name,
                                          int64_t *value);
inline bool DefaultGetProfileIntegerCapV1(PlatformMethods *platform,
                                         const char *name,
                                         int64_t *value)
{
    return false;
}

// Platform methods are enumerated here once.
""")
        source = replace_once(source,
                              "    OP(recordShaderCacheUse, RecordShaderCacheUse)\n",
                              "    OP(recordShaderCacheUse, RecordShaderCacheUse)               \\\n    OP(getProfileIntegerCapV1, GetProfileIntegerCapV1)\n")
        source = replace_once(source, """// No further uses of platform methods is allowed.  EGL extensions should be used instead.  While
// methods are being removed, use PlaceholderCallback to keep the layout of PlatformMethods
// constant.
static_assert(g_NumPlatformMethods == 18, "Avoid adding methods to PlatformMethods");
""", """// The fork appends one versioned callback to the frozen upstream prefix. The
// existing name/count handshake rejects an older ANGLE library. Keep all 18
// upstream slots, including placeholders, at their original offsets.
static_assert(g_NumPlatformMethods == 19, "Update the matched embedder callback ABI");
""")
    elif path.endswith("angle_platform_impl.cc"):
        source = replace_once(source, '#include "base/base64.h"\n',
                              '#include "base/apostate/profile.h"\n#include "base/base64.h"\n')
        source = replace_once(source, "std::atomic<bool> g_post_task_failed_for_testing{false};\n", """std::atomic<bool> g_post_task_failed_for_testing{false};

bool ANGLEPlatformImpl_getProfileIntegerCapV1(PlatformMethods* platform,
                                             const char* name,
                                             int64_t* value) {
  if (!name || !value) {
    return false;
  }
  const auto* profile = base::apostate::Profile::Get();
  if (!profile) {
    return false;
  }
  const auto it = profile->gl_limits().find(name);
  if (it == profile->gl_limits().end()) {
    return false;
  }
  *value = it->second;
  return true;
}
""")
        source = replace_once(source,
                              "  platformMethods->currentTime = ANGLEPlatformImpl_currentTime;\n",
                              """  platformMethods->getProfileIntegerCapV1 =
      ANGLEPlatformImpl_getProfileIntegerCapV1;
  platformMethods->currentTime = ANGLEPlatformImpl_currentTime;
""")
    elif path.endswith("Context.cpp"):
        source = replace_once(source, '#include "libANGLE/validationES.h"\n',
                              '#include "libANGLE/validationES.h"\n#include "platform/PlatformMethods.h"\n')
        source = replace_once(source, "namespace gl\n{\nnamespace\n{\n", """namespace gl
{
namespace
{
bool GetProfileIntegerCap(const char *name, int64_t *value)
{
    angle::PlatformMethods *platform = ANGLEPlatformCurrent();
    return platform->getProfileIntegerCapV1(platform, name, value) && *value > 0;
}

void ApplyProfileIntegerCaps(Caps *caps)
{
    int64_t width = 0;
    int64_t height = 0;
    const bool hasWidth = GetProfileIntegerCap("MAX_VIEWPORT_DIMS_WIDTH", &width);
    const bool hasHeight = GetProfileIntegerCap("MAX_VIEWPORT_DIMS_HEIGHT", &height);
    if (!hasWidth && !hasHeight)
    {
        // Legacy profiles represented square viewports with one integer.
        if (GetProfileIntegerCap("MAX_VIEWPORT_DIMS", &width))
        {
            height = width;
        }
    }
    if (width > 0 && height > 0)
    {
        caps->maxViewportWidth =
            static_cast<GLint>(std::min<int64_t>(caps->maxViewportWidth, width));
        caps->maxViewportHeight =
            static_cast<GLint>(std::min<int64_t>(caps->maxViewportHeight, height));
    }

    int64_t blockSize = 0;
    if (GetProfileIntegerCap("MAX_UNIFORM_BLOCK_SIZE", &blockSize))
    {
        caps->maxUniformBlockSize = std::min<GLint64>(caps->maxUniformBlockSize, blockSize);
    }

    int64_t components = 0;
    int64_t vectors = 0;
    const bool hasComponents =
        GetProfileIntegerCap("MAX_VERTEX_UNIFORM_COMPONENTS", &components);
    const bool hasVectors = GetProfileIntegerCap("MAX_VERTEX_UNIFORM_VECTORS", &vectors);
    if ((hasComponents && components >= 4) || hasVectors)
    {
        int64_t effectiveVectors = caps->maxVertexUniformVectors;
        if (hasComponents && components >= 4)
        {
            effectiveVectors = std::min(effectiveVectors, components / 4);
        }
        if (hasVectors)
        {
            effectiveVectors = std::min(effectiveVectors, vectors);
        }
        // A GLES2 backend can supply vector limits without a component limit.
        // Keep that unsupported component cap at zero, without erasing valid
        // vector storage. Profile components still restrict the vector bound.
        int64_t effectiveComponents = caps->maxShaderUniformComponents[ShaderType::Vertex];
        if (effectiveComponents > 0)
        {
            if (hasComponents && components >= 4)
            {
                effectiveComponents = std::min(effectiveComponents, components);
            }
            effectiveVectors = std::min(effectiveVectors, effectiveComponents / 4);
            effectiveComponents = std::min(effectiveComponents, effectiveVectors * 4);
            caps->maxShaderUniformComponents[ShaderType::Vertex] =
                static_cast<GLint>(effectiveComponents);
        }
        caps->maxVertexUniformVectors = static_cast<GLint>(effectiveVectors);
    }
}

""")
        source = replace_once(source,
                              "    // Initialize ANGLE_shader_pixel_local_storage caps based on frontend GL queries.\n",
                              """    // The embedder installs this callback before eglInitialize. Native and
    // implementation limits are already applied; state, compiler resources and
    // link validation will consume these same reduced caps.
    ApplyProfileIntegerCaps(caps);

    // Initialize ANGLE_shader_pixel_local_storage caps based on frontend GL queries.
""")
    elif path.endswith("MemoryProgramCache.cpp"):
        source = replace_once(source, "    // Hash pre-link program properties.\n", """    // Uniform block size is checked at link time and is not part of the shader
    // resource key. Prevent a cached program linked under wider profile limits
    // from bypassing the current context's validation.
    const Caps &caps = context->getCaps();
    angle::UpdateHashWithValue(hasher, caps.maxUniformBlockSize);
    angle::UpdateHashWithValue(hasher, caps.maxVertexUniformVectors);
    angle::UpdateHashWithValue(hasher, caps.maxShaderUniformComponents[ShaderType::Vertex]);

    // Hash pre-link program properties.
""")
    return source


HEADER = """Subject: [PATCH] angle: apply profile reductions before caps initialize resources

The Mac capture reports a 16384 viewport, 16384-byte uniform blocks and
4096 vertex uniform components. The GL query hooks miss the first two
parameters and do not reduce the ANGLE state used for viewport and shader
validation. Passthrough queries also use distinct robust entrypoints.

Read these limits through a typed same-process platform callback installed by
Chromium before eglInitialize. ANGLE reduces its effective caps before state
and compiler initialization. Chromium retains the only profile loader; ANGLE
does not link base, parse JSON, add a GL extension or expose a new entrypoint.
The appended V1 callback is negotiated through the existing name/count check.

Keep viewport width/height distinct and vertex vectors/components consistent.
Hash effective uniform limits into program cache keys so a cached permissive
link cannot bypass a later profile's stricter block-size limit. Native limits
below the reference remain below it. Point-size and precision behavior are
outside this patch.

Reference: resources/fingerprints/raw/m4-max-chrome-20260908T163229Z.json
Ledger proposal: gpu.clamp-conformance. V1 and behavioral V3 remain required.

"""


def check_cxx(scratch, modified):
    """Compile the actual callback header and reducer with minimal GL type stubs.

    This checks ABI syntax and reduction arithmetic; it is not the Chromium V1
    gate or an ANGLE rendering test.
    """
    compiler = shutil.which("clang++") or shutil.which("c++")
    if not compiler:
        raise RuntimeError("--check-cxx requires clang++ or c++")
    header = scratch / "PlatformMethods.h"
    header.write_text(modified["third_party/angle/include/platform/PlatformMethods.h"])
    context = modified["third_party/angle/src/libANGLE/Context.cpp"]
    start = context.index("bool GetProfileIntegerCap(")
    end = context.index("constexpr state::DirtyObjects", start)
    reducer = context[start:end]
    harness = r'''#include "PlatformMethods.h"
#include <algorithm>
#include <cassert>
#include <cstddef>
#include <limits>
#include <map>
#include <string>

using GLint = int;
using GLint64 = int64_t;
enum class ShaderType { Vertex };
struct StageComponents {
    GLint vertex;
    GLint &operator[](ShaderType) { return vertex; }
};
struct Caps {
    GLint maxViewportWidth = 32767;
    GLint maxViewportHeight = 32767;
    GLint64 maxUniformBlockSize = 65536;
    GLint maxVertexUniformVectors = 4096;
    StageComponents maxShaderUniformComponents{16384};
};
angle::PlatformMethods methods;
std::map<std::string, int64_t> claims;
angle::PlatformMethods *ANGLEPlatformCurrent() { return &methods; }
bool ReadCap(angle::PlatformMethods *, const char *name, int64_t *value) {
    auto it = claims.find(name);
    if (it == claims.end()) return false;
    *value = it->second;
    return true;
}
'''
    harness += reducer
    harness += r'''
int main() {
    static_assert(angle::g_NumPlatformMethods == 19);
    static_assert(offsetof(angle::PlatformMethods, getProfileIntegerCapV1) ==
                  19 * sizeof(void *));
    assert(std::string(angle::g_PlatformMethodNames[18]) == "getProfileIntegerCapV1");
    int64_t untouched = 123;
    assert(!methods.getProfileIntegerCapV1(&methods, "absent", &untouched));
    assert(untouched == 123);
    Caps native;
    ApplyProfileIntegerCaps(&native);
    assert(native.maxViewportWidth == 32767 && native.maxViewportHeight == 32767);
    assert(native.maxUniformBlockSize == 65536);
    assert(native.maxVertexUniformVectors == 4096);
    assert(native.maxShaderUniformComponents.vertex == 16384);

    methods.getProfileIntegerCapV1 = ReadCap;
    claims = {{"MAX_VIEWPORT_DIMS_WIDTH", 16384}, {"MAX_VIEWPORT_DIMS_HEIGHT", 8192},
              {"MAX_UNIFORM_BLOCK_SIZE", 16384}, {"MAX_VERTEX_UNIFORM_COMPONENTS", 4096}};
    Caps reduced;
    ApplyProfileIntegerCaps(&reduced);
    assert(reduced.maxViewportWidth == 16384 && reduced.maxViewportHeight == 8192);
    assert(reduced.maxUniformBlockSize == 16384);
    assert(reduced.maxVertexUniformVectors == 1024);
    assert(reduced.maxShaderUniformComponents.vertex == 4096);

    Caps smaller{8192, 4096, 8192, 512, {2048}};
    ApplyProfileIntegerCaps(&smaller);
    assert(smaller.maxViewportWidth == 8192 && smaller.maxViewportHeight == 4096);
    assert(smaller.maxUniformBlockSize == 8192);
    assert(smaller.maxVertexUniformVectors == 512);
    assert(smaller.maxShaderUniformComponents.vertex == 2048);

    // GLES2 may have vector storage but no component/UBO limit. A profile
    // must not turn that unsupported component query into zero shader vectors.
    claims = {{"MAX_UNIFORM_BLOCK_SIZE", 16384},
              {"MAX_VERTEX_UNIFORM_COMPONENTS", 4096},
              {"MAX_VERTEX_UNIFORM_VECTORS", 1024}};
    Caps es2{8192, 8192, 0, 256, {0}};
    ApplyProfileIntegerCaps(&es2);
    assert(es2.maxVertexUniformVectors == 256);
    assert(es2.maxShaderUniformComponents.vertex == 0);
    assert(es2.maxUniformBlockSize == 0);
    claims = {{"MAX_VERTEX_UNIFORM_COMPONENTS", 512}};
    ApplyProfileIntegerCaps(&es2);
    assert(es2.maxVertexUniformVectors == 128);
    assert(es2.maxShaderUniformComponents.vertex == 0);
    assert(es2.maxUniformBlockSize == 0);
    claims = {{"MAX_VERTEX_UNIFORM_VECTORS", 64}};
    ApplyProfileIntegerCaps(&es2);
    assert(es2.maxVertexUniformVectors == 64);
    assert(es2.maxShaderUniformComponents.vertex == 0);

    claims = {{"MAX_VIEWPORT_DIMS", 16384}};
    Caps square;
    ApplyProfileIntegerCaps(&square);
    assert(square.maxViewportWidth == 16384 && square.maxViewportHeight == 16384);
    claims = {{"MAX_VIEWPORT_DIMS_WIDTH", 16384}};
    Caps partial;
    ApplyProfileIntegerCaps(&partial);
    assert(partial.maxViewportWidth == 32767 && partial.maxViewportHeight == 32767);

    claims = {{"MAX_VERTEX_UNIFORM_COMPONENTS", 4096}, {"MAX_VERTEX_UNIFORM_VECTORS", 768}};
    Caps vectors;
    ApplyProfileIntegerCaps(&vectors);
    assert(vectors.maxVertexUniformVectors == 768);
    assert(vectors.maxShaderUniformComponents.vertex == 3072);

    claims = {{"MAX_VIEWPORT_DIMS", std::numeric_limits<int64_t>::max()},
              {"MAX_UNIFORM_BLOCK_SIZE", std::numeric_limits<int64_t>::max()},
              {"MAX_VERTEX_UNIFORM_COMPONENTS", std::numeric_limits<int64_t>::max()},
              {"MAX_VERTEX_UNIFORM_VECTORS", std::numeric_limits<int64_t>::max()}};
    Caps wide;
    ApplyProfileIntegerCaps(&wide);
    assert(wide.maxViewportWidth == 32767 && wide.maxViewportHeight == 32767);
    assert(wide.maxUniformBlockSize == 65536);
    assert(wide.maxVertexUniformVectors == 4096);
    assert(wide.maxShaderUniformComponents.vertex == 16384);
}
'''
    source = scratch / "caps_check.cc"
    source.write_text(harness)
    binary = scratch / "caps_check"
    subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-parameter", str(source), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=pathlib.Path,
                        default=ROOT / "patches/0039-angle-profile-caps.patch")
    parser.add_argument("--check-cxx", action="store_true",
                        help="compile callback ABI and reduction boundary checks")
    args = parser.parse_args()

    def fetch(item):
        path, (repo, revision, remote_path) = item
        url = f"https://chromium.googlesource.com/{repo}/+/{revision}/{remote_path}?format=TEXT"
        with urllib.request.urlopen(url, timeout=30) as response:
            source = base64.b64decode(response.read()).decode()
        return path, source

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        sources = dict(pool.map(fetch, FILES.items()))
    with tempfile.TemporaryDirectory(prefix="apostate-caps-0039-") as temp:
        scratch = pathlib.Path(temp)
        for path, source in sources.items():
            target = scratch / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source)
        selected = [f"--include={path}" for path in FILES]
        series = (ROOT / "patches/series").read_text().splitlines()
        for patch in series:
            if not patch or patch.startswith("#"):
                continue
            if int(patch[:4]) > 38:
                break
            subprocess.run(["git", "apply", *selected, str(ROOT / "patches" / patch)],
                           cwd=scratch, check=True, capture_output=True, text=True)
        result = [HEADER]
        modified = {}
        for path in FILES:
            before = (scratch / path).read_text()
            after = edit(path, before)
            modified[path] = after
            result.append(f"diff --git a/{path} b/{path}\n")
            result.extend(difflib.unified_diff(before.splitlines(keepends=True),
                                              after.splitlines(keepends=True),
                                              fromfile=f"a/{path}", tofile=f"b/{path}"))
        generated = scratch / "0039.patch"
        generated.write_text("".join(result))
        subprocess.run(["git", "apply", "--check", str(generated)],
                       cwd=scratch, check=True, capture_output=True, text=True)
        if args.check_cxx:
            check_cxx(scratch, modified)
        args.out.write_text(generated.read_text())
    print(f"generated and checked {args.out}")


if __name__ == "__main__":
    main()
