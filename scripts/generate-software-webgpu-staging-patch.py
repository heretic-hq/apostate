#!/usr/bin/env python3
"""Generate the profile CPU-provider shared-image admission patch from pinned source."""
import argparse
import difflib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'patches/0064-profile-webgpu-shared-image-staging.patch')
    args = parser.parse_args()
    prefix = 'gpu/command_buffer/service/shared_image/gl_texture_image_backing_factory.'
    changes = {}
    for suffix in ('cc', 'h'):
        name = prefix + suffix
        original = (args.source / name).read_text()
        modified = original
        if suffix == 'h':
            modified = replace_once(modified, '  const bool supports_cpu_upload_;', '''  const bool supports_cpu_upload_;
  // Admission for the existing Skia readback/upload path used by CPU adapters.
  const bool profile_software_webgpu_;''')
        else:
            modified = replace_once(modified, '#include "build/build_config.h"', '''#include "base/apostate/profile.h"
#include "build/build_config.h"
#include "ui/gl/buildflags.h"
#if BUILDFLAG(IS_LINUX) && BUILDFLAG(USE_DAWN)
#include "gpu/command_buffer/service/profile_software_webgpu_policy.h"
#endif''')
            modified = replace_once(modified, '      support_all_metal_usages_(false) {}', '''      profile_software_webgpu_([&gpu_preferences] {
#if BUILDFLAG(IS_LINUX) && BUILDFLAG(USE_DAWN)
        const auto* profile = base::apostate::Profile::Get();
        if (!profile || !profile->ua_platform() ||
            gpu_preferences.enable_webgpu_on_vk_via_gl_interop) {
          return false;
        }
        return webgpu::CanUseProfileSoftwareWebGPU({
            .platform = *profile->ua_platform(),
            .angle_is_swiftshader =
                gl::GetANGLEImplementation() == gl::ANGLEImplementation::kSwiftShader,
            // IsSupported checks the actual requesting Skia context below.
            .shared_context_is_gl = true,
            .safe = !gpu_preferences.enable_unsafe_webgpu &&
                    !gpu_preferences.enable_webgpu_developer_features &&
                    !gpu_preferences.enable_webgpu_experimental_features,
            .has_dawn_overrides = !gpu_preferences.enabled_dawn_features_list.empty() ||
                !gpu_preferences.disabled_dawn_features_list.empty() ||
                !features::kWebGPUEnabledToggles.Get().empty() ||
                !features::kWebGPUDisabledToggles.Get().empty() ||
                !features::kWebGPUUnsafeFeatures.Get().empty(),
            .force_compat = gpu_preferences.force_webgpu_compat,
            .requested_adapter = gpu_preferences.use_webgpu_adapter,
        });
#else
        return false;
#endif
      }()),
      support_all_metal_usages_(false) {}''')
            modified = replace_once(modified, '''  // Only supports WebGPU usages on ANGLE/GL on a Skia/GL context
  if (usage.HasAny(kWebGPUUsages)) {''', '''  // CPU WebGPU devices already associate mailboxes through Skia staging in
  // WebGPUDecoderImpl. Admit only the three formats supported by that path;
  // allocation, clearing, size and access checks remain below and upstream.
  const bool cpu_staging = profile_software_webgpu_ &&
      gr_context_type == GrContextType::kGL &&
      gl::GetGLImplementation() == gl::kGLImplementationEGLANGLE &&
      gl::GetANGLEImplementation() == gl::ANGLEImplementation::kSwiftShader &&
      (format == viz::SinglePlaneFormat::kBGRA_8888 ||
       format == viz::SinglePlaneFormat::kRGBA_8888 ||
       format == viz::SinglePlaneFormat::kRGBA_F16);
  // Other WebGPU usages retain the native ANGLE/GL interop requirements.
  if (usage.HasAny(kWebGPUUsages) && !cpu_staging) {''')
        changes[name] = (original, modified)
    patch = ''
    for name, (original, modified) in changes.items():
        patch += f'diff --git a/{name} b/{name}\n'
        patch += ''.join(difflib.unified_diff(original.splitlines(True), modified.splitlines(True),
                                             fromfile='a/' + name, tofile='b/' + name))
    args.output.write_text(patch)


if __name__ == '__main__':
    main()
