#!/usr/bin/env python3
"""Generate0063 from immutable ANGLE and the earlier patch prefix, scratch only."""
import argparse,base64,difflib,hashlib,json,subprocess,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PIN='7df613367a1d4ca9aea9ece344d4580d32d132a9'
PATHS=['src/libANGLE/Caps.h','src/libANGLE/Context.cpp','src/libANGLE/renderer/vulkan/vk_caps_utils.cpp',
       'src/libANGLE/renderer/vulkan/VertexArrayVk.cpp','src/tests/BUILD.gn']

def once(s,a,b):
    assert s.count(a)==1,a[:100]
    return s.replace(a,b)

def change(path,s):
    if path.endswith('Caps.h'):
        return once(s,'    GLint64 maxElementIndex       = 0;','''    GLint64 maxElementIndex       = 0;
    // Internal backend capability, usable only with explicit frontend buffer
    // access validation. Zero means no capability beyond maxElementIndex.
    GLint64 maxValidatedElementIndex = 0;''')
    if path.endswith('vk_caps_utils.cpp'):
        return once(s,'    mNativeCaps.maxElementIndex = (1 << 30) - 1;','''    mNativeCaps.maxElementIndex = (1 << 30) - 1;
    // Keep the default limit. The pinned software implementation carries full
    // uint32 indices; only validated WebGL contexts may select this capability.
    if (IsLinux() && sizeof(size_t) == 8 &&
        mEnabledICD == angle::vk::ICD::SwiftShader &&
        mPhysicalDeviceProperties.deviceType == VK_PHYSICAL_DEVICE_TYPE_CPU &&
        IsSwiftshader(mPhysicalDeviceProperties.vendorID, mPhysicalDeviceProperties.deviceID) &&
        mPhysicalDeviceFeatures.fullDrawIndexUint32 == VK_TRUE &&
        limitsVk.maxDrawIndexedIndexValue == std::numeric_limits<uint32_t>::max())
    {
        mNativeCaps.maxValidatedElementIndex =
            static_cast<GLint64>(std::numeric_limits<GLuint>::max()) - 1;
    }''')
    if path.endswith('Context.cpp'):
        return once(s,'''    mBufferAccessValidationEnabled =
        !mSupportedExtensions.robustBufferAccessBehaviorKHR && mRequiresRobustBehavior;
''','''    mBufferAccessValidationEnabled =
        !mSupportedExtensions.robustBufferAccessBehaviorKHR && mRequiresRobustBehavior;

    if (caps->maxValidatedElementIndex > 0)
    {
        // Re-evaluate after robustness/extension changes, before rebuilding the
        // validation cache. Unprofiled and unvalidated contexts retain defaults.
        caps->maxElementIndex = mImplementation->getNativeCaps().maxElementIndex;
        int64_t requested = 0;
        if (mState.isWebGL() && mBufferAccessValidationEnabled && !skipValidation() &&
            GetProfileIntegerCap("MAX_ELEMENT_INDEX", &requested))
        {
            caps->maxElementIndex =
                std::min<GLint64>(requested, caps->maxValidatedElementIndex);
        }
    }
''')
    if path.endswith('VertexArrayVk.cpp'):
        return once(s,'''    GLint startVertex;
    size_t vertexCount;
    ANGLE_TRY(GetVertexRangeInfo(context, firstVertex, vertexOrIndexCount, indexTypeOrInvalid,
                                 indices, 0, &startVertex, &vertexCount));

    ASSERT(vertexCount > 0);
    const auto &attribs  = mState.getVertexAttributes();
    const auto &bindings = mState.getVertexBindings();
''','''    const auto &attribs  = mState.getVertexAttributes();
    const auto &bindings = mState.getVertexBindings();
    bool needsVertexRange = true;
    if (context->getState().isWebGL() && context->isBufferAccessValidationEnabled() &&
        context->getCaps().maxValidatedElementIndex > 0 &&
        context->getCaps().maxElementIndex > std::numeric_limits<GLint>::max())
    {
        // Emulated instance divisors consume instance-indexed data only. Do not
        // truncate or reject an unrelated uint32 vertex index while streaming it.
        needsVertexRange = false;
        for (size_t attribIndex : activeStreamedAttribs)
        {
            if (bindings[attribs[attribIndex].bindingIndex].getDivisor() == 0)
            {
                needsVertexRange = true;
                break;
            }
        }
    }
    GLint startVertex = 0;
    size_t vertexCount = 1;
    if (needsVertexRange)
    {
        ANGLE_TRY(GetVertexRangeInfo(context, firstVertex, vertexOrIndexCount, indexTypeOrInvalid,
                                     indices, 0, &startVertex, &vertexCount));
    }
    ASSERT(vertexCount > 0);
''')
    if path.endswith('BUILD.gn'):
        return s+'''
# Small indexed draws against the pinned software provider, with normal CFI.
if (is_linux && angle_enable_vulkan && angle_has_rapidjson) {
  angle_test("apostate_large_index_tests") {
    testonly = true
    include_dirs = [ "." ]
    sources = [
      "apostate_large_index_tests_main.cpp",
      "gl_tests/ApostateLargeElementIndexTest.cpp",
      "test_utils/ANGLETest.cpp",
      "test_utils/system_info_util.cpp",
    ]
    defines = []
    if (angle_use_partition_alloc) {
      defines += [ "ANGLE_USE_PARTITION_ALLOC" ]
    }
    configs += [ "${angle_root}:libANGLE_config" ]
    deps = [
      ":angle_common_test_utils_static",
      "$angle_root:angle_static",
      "$angle_root:angle_gl_enum_utils",
      "$angle_root:angle_image_util",
    ]
    data = [ "angle_end2end_tests_expectations.txt" ]
    data_deps = [ "$angle_root/src/common/vulkan:angle_vulkan_icd" ]
  }
}
'''
    raise AssertionError(path)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--scratch',required=True,type=Path)
    p.add_argument('--output',type=Path,default=ROOT/'patches/0063-validated-software-element-indices.patch')
    a=p.parse_args();a.scratch.mkdir(parents=True,exist_ok=False)
    for path in PATHS:
        content=base64.b64decode(urllib.request.urlopen(f'https://chromium.googlesource.com/angle/angle/+/{PIN}/{path}?format=TEXT',timeout=30).read())
        f=a.scratch/'third_party/angle'/path;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(content)
    subprocess.run(['git','init','-q',str(a.scratch)],check=True)
    inc=['--include=third_party/angle/'+p for p in PATHS]
    for patch in (ROOT/'patches/series').read_text().splitlines():
        if not patch or patch.startswith('#') or patch[:4]>='0063':continue
        subprocess.run(['git','apply',*inc,str(ROOT/'patches'/patch)],cwd=a.scratch,capture_output=True,check=True)
    changes={}
    for path in PATHS:
        f=a.scratch/'third_party/angle'/path;before=f.read_text();changes['third_party/angle/'+path]=(before,change(path,before))
    for name,target in [('tests.cpp','src/tests/gl_tests/ApostateLargeElementIndexTest.cpp'),('main.cpp','src/tests/apostate_large_index_tests_main.cpp')]:
        changes['third_party/angle/'+target]=(None,(ROOT/'scripts/templates/large-element-index'/name).read_text())
    patch=''
    for path,(before,after) in sorted(changes.items()):
        patch+=f'diff --git a/{path} b/{path}\n'
        if before is None:patch+='new file mode 100644\n'
        patch+=''.join(difflib.unified_diff((before or '').splitlines(True),after.splitlines(True),fromfile='a/'+path if before is not None else '/dev/null',tofile='b/'+path))
    a.output.write_text(patch)
    subprocess.run(['git','apply','--check',str(a.output.resolve())],cwd=a.scratch,check=True)
    subprocess.run(['git','apply',str(a.output.resolve())],cwd=a.scratch,check=True)
    print(json.dumps({'sha256':hashlib.sha256(a.output.read_bytes()).hexdigest(),'files':len(changes),'scratch':str(a.scratch)}))
if __name__=='__main__':main()
