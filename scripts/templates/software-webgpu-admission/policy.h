// Copyright 2026 The Apostate Authors
// Use of this source code is governed by a BSD-style license.
#ifndef GPU_COMMAND_BUFFER_SERVICE_PROFILE_SOFTWARE_WEBGPU_POLICY_H_
#define GPU_COMMAND_BUFFER_SERVICE_PROFILE_SOFTWARE_WEBGPU_POLICY_H_

#include <string_view>
#include "gpu/config/gpu_preferences.h"
#include "gpu/config/webgpu_blocklist_impl.h"
#include "third_party/dawn/include/dawn/webgpu_cpp.h"

namespace gpu::webgpu {
struct ProfileSoftwareWebGPUContext {
  std::string_view platform;
  bool angle_is_swiftshader = false;
  bool shared_context_is_gl = false;
  bool safe = false;
  bool has_dawn_overrides = false;
  bool force_compat = false;
  WebGPUAdapterName requested_adapter = WebGPUAdapterName::kDefault;
};

inline bool CanUseProfileSoftwareWebGPU(const ProfileSoftwareWebGPUContext& context) {
  return (context.platform == "Windows" || context.platform == "macOS") &&
         context.angle_is_swiftshader && context.shared_context_is_gl && context.safe &&
         !context.has_dawn_overrides && !context.force_compat &&
         (context.requested_adapter == WebGPUAdapterName::kDefault ||
          context.requested_adapter == WebGPUAdapterName::kSwiftShader);
}

// The exclusive instance flag establishes factory/module-path provenance.
// Numeric adapter identity is an additional check, never the sole authority.
inline WebGPUBlocklistOptions ProfileSoftwareWebGPUBlocklistOptions(
    bool exclusive_swiftshader_instance, const wgpu::AdapterInfo& info) {
  WebGPUBlocklistOptions options;
  if (exclusive_swiftshader_instance && info.backendType == wgpu::BackendType::Vulkan &&
      info.adapterType == wgpu::AdapterType::CPU && info.vendorID == 0x1ae0 &&
      info.deviceID == 0xc0de) {
    options.ignores = WebGPUBlocklistReason::CPUAdapter;
  }
  return options;
}
}  // namespace gpu::webgpu
#endif  // GPU_COMMAND_BUFFER_SERVICE_PROFILE_SOFTWARE_WEBGPU_POLICY_H_
