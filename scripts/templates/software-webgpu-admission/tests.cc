#if BUILDFLAG(IS_LINUX)
TEST(ProfileSoftwareWebGPUPolicyTest, RequiresProfileAndActualSoftwareContext) {
  webgpu::ProfileSoftwareWebGPUContext context;
  context.platform = "Windows";
  context.angle_is_swiftshader = true;
  context.shared_context_is_gl = true;
  context.safe = true;
  EXPECT_TRUE(webgpu::CanUseProfileSoftwareWebGPU(context));
  context.platform = "macOS";
  EXPECT_TRUE(webgpu::CanUseProfileSoftwareWebGPU(context));
  for (const char* platform : {"", "Linux", "Android"}) {
    context.platform = platform;
    EXPECT_FALSE(webgpu::CanUseProfileSoftwareWebGPU(context));
  }
  context.platform = "Windows";
  context.angle_is_swiftshader = false;
  EXPECT_FALSE(webgpu::CanUseProfileSoftwareWebGPU(context));
  context.angle_is_swiftshader = true;
  context.shared_context_is_gl = false;
  EXPECT_FALSE(webgpu::CanUseProfileSoftwareWebGPU(context));
}

TEST(ProfileSoftwareWebGPUPolicyTest, RetainsSafetyAndExplicitAdapterChoices) {
  webgpu::ProfileSoftwareWebGPUContext context;
  context.platform = "Windows";
  context.angle_is_swiftshader = context.shared_context_is_gl = context.safe = true;
  context.safe = false;
  EXPECT_FALSE(webgpu::CanUseProfileSoftwareWebGPU(context));
  context.safe = true;
  context.has_dawn_overrides = true;
  EXPECT_FALSE(webgpu::CanUseProfileSoftwareWebGPU(context));
  context.has_dawn_overrides = false;
  context.force_compat = true;
  EXPECT_FALSE(webgpu::CanUseProfileSoftwareWebGPU(context));
  context.force_compat = false;
  for (auto adapter : {WebGPUAdapterName::kD3D11, WebGPUAdapterName::kOpenGLES}) {
    context.requested_adapter = adapter;
    EXPECT_FALSE(webgpu::CanUseProfileSoftwareWebGPU(context));
  }
  context.requested_adapter = WebGPUAdapterName::kSwiftShader;
  EXPECT_TRUE(webgpu::CanUseProfileSoftwareWebGPU(context));
}

TEST(ProfileSoftwareWebGPUPolicyTest, OnlyApprovedCPUReasonIsIgnored) {
  auto make_info = [](WGPUAdapterType type, uint32_t vendor) {
    WGPUAdapterInfo raw = {};
    raw.backendType = WGPUBackendType_Vulkan;
    raw.adapterType = type;
    raw.vendorID = vendor;
    raw.deviceID = 0xc0de;
    raw.description = {"", 0};
    raw.architecture = {"", 0};
    return raw;
  };
  // Match this file's existing C-ABI fixture pattern. Fully initialize each
  // independent POD value before creating a read-only, layout-compatible view.
  // No immutable C++ output object is mutated or made to own these strings.
  auto view = [](const WGPUAdapterInfo& raw) -> const wgpu::AdapterInfo& {
    return *reinterpret_cast<const wgpu::AdapterInfo*>(&raw);
  };
  const WGPUAdapterInfo approved = make_info(WGPUAdapterType_CPU, 0x1ae0);
  const auto& info = view(approved);
  auto denied = webgpu::ProfileSoftwareWebGPUBlocklistOptions(false, info);
  EXPECT_EQ(denied.ignores, WebGPUBlocklistReason::None);
  EXPECT_TRUE(gpu::IsWebGPUAdapterBlocklisted(info, denied).blocked);
  auto admitted = webgpu::ProfileSoftwareWebGPUBlocklistOptions(true, info);
  EXPECT_EQ(admitted.ignores, WebGPUBlocklistReason::CPUAdapter);
  EXPECT_FALSE(gpu::IsWebGPUAdapterBlocklisted(info, admitted).blocked);
  admitted.blocklist_string = "1ae0:c0de";
  EXPECT_TRUE(gpu::IsWebGPUAdapterBlocklisted(info, admitted).blocked);
  const WGPUAdapterInfo other_cpu = make_info(WGPUAdapterType_CPU, 0x1002);
  EXPECT_EQ(webgpu::ProfileSoftwareWebGPUBlocklistOptions(true, view(other_cpu)).ignores,
            WebGPUBlocklistReason::None);
  const WGPUAdapterInfo physical = make_info(WGPUAdapterType_DiscreteGPU, 0x1ae0);
  EXPECT_EQ(webgpu::ProfileSoftwareWebGPUBlocklistOptions(true, view(physical)).ignores,
            WebGPUBlocklistReason::None);
}

TEST(ProfileSoftwareWebGPUPolicyTest, ExclusiveDescriptorParticipatesInIdentity) {
  dawn::native::DawnInstanceDescriptor normal;
  dawn::native::DawnInstanceDescriptor exclusive;
  EXPECT_TRUE(normal == exclusive);
  exclusive.onlyUseSwiftShader = true;
  EXPECT_FALSE(normal == exclusive);
}

TEST(ProfileSoftwareWebGPUPolicyTest, InvalidExclusivePathsNeverDiscoverAdapters) {
  const char* relative[] = {"relative/"};
  const char* multiple[] = {"/first/", "/second/"};
  for (unsigned variant : {0u, 1u, 2u}) {
    dawn::native::DawnInstanceDescriptor native_desc;
    native_desc.onlyUseSwiftShader = true;
    native_desc.additionalRuntimeSearchPathsCount = variant;
    native_desc.additionalRuntimeSearchPaths = variant == 1 ? relative : multiple;
    wgpu::InstanceDescriptor desc;
    desc.nextInChain = &native_desc;
    dawn::native::Instance instance(&desc);
    wgpu::RequestAdapterOptions options;
    options.backendType = wgpu::BackendType::Vulkan;
    options.forceFallbackAdapter = true;
    EXPECT_TRUE(instance.EnumerateAdapters(&options).empty());
  }
}

TEST(ProfileSoftwareWebGPUPolicyTest, ExclusiveInstanceCannotProbeOtherBackends) {
  const char* paths[] = {"/not-probed/"};
  dawn::native::DawnInstanceDescriptor native_desc;
  native_desc.onlyUseSwiftShader = true;
  native_desc.additionalRuntimeSearchPathsCount = 1;
  native_desc.additionalRuntimeSearchPaths = paths;
  wgpu::InstanceDescriptor desc;
  desc.nextInChain = &native_desc;
  dawn::native::Instance instance(&desc);
  wgpu::RequestAdapterOptions options;
  options.backendType = wgpu::BackendType::OpenGLES;
  EXPECT_TRUE(instance.EnumerateAdapters(&options).empty());
}
#endif  // BUILDFLAG(IS_LINUX)
