// Diagnostic arithmetic model of the measured macOS ARM log10f routine.
// The published Libm ARM logf source is empty. This model follows the
// observed normal-input reduction, numeric constants and explicit f64 FMA
// order. It is not a claim about every Apple OS version or rounding mode.
// These65 reciprocal/logarithm pairs are range-reduction constants for all
// positive inputs, not captured function results indexed by input cases.
#pragma once
#include <bit>
#include <cmath>
#include <cfenv>
#include <cstdint>
namespace mac_arm_log10 {
inline bool FlushInputs(){
#if defined(__aarch64__)
 uint64_t value;asm volatile("mrs %0, fpcr":"=r"(value));return value&(1ull<<24);
#elif defined(__x86_64__)
 uint32_t value;asm volatile("stmxcsr %0":"=m"(value));return value&0x40;
#else
 return false;
#endif
}
inline float Evaluate(float value){
 uint32_t word=std::bit_cast<uint32_t>(value);
 if(word-0x00800000u>=0x7f000000u){
  uint32_t magnitude=word&0x7fffffffu;
  if(magnitude>0x7f800000u){
   if(!(word&0x00400000u))std::feraiseexcept(FE_INVALID);
   return value;
  }
  if(magnitude==0 || (magnitude<0x00800000u && FlushInputs())){
   std::feraiseexcept(FE_DIVBYZERO);return -INFINITY;
  }
  if(word&0x80000000u){std::feraiseexcept(FE_INVALID);return std::bit_cast<float>(0x7fc00000u);}
  if(magnitude==0x7f800000u)return value;
  // Exact integer-mantissa normalization used by the native subnormal path.
  float normalized=std::bit_cast<float>(word|0x3f800000u)-1.f;
  word=std::bit_cast<uint32_t>(normalized)-0x3f000000u;
 }
 struct Entry{double reciprocal,logarithm;};
 static constexpr Entry table[]={
  {0x1.0000000000000p+0,0x0.0p+0},
  {0x1.f81f81f81a3e5p-1,0x1.b9476a50f6475p-8},
  {0x1.f07c1f07c3108p-1,0x1.b5e908eaf3398p-7},
  {0x1.e9131abf09b55p-1,0x1.45f4f5acd2447p-6},
  {0x1.e1e1e1e1e0ae1p-1,0x1.af5f92b020224p-6},
  {0x1.dae6076b8f76bp-1,0x1.0ba01a81b0d1dp-5},
  {0x1.d41d41d410288p-1,0x1.3ed1199ac1d02p-5},
  {0x1.cd8568902fb3fp-1,0x1.714834281fa6cp-5},
  {0x1.c71c71c70f481p-1,0x1.a30a9d6105e4bp-5},
  {0x1.c0e0703826caap-1,0x1.d41d5164a5b23p-5},
  {0x1.bacf914c177b9p-1,0x1.02428c1f18d98p-4},
  {0x1.b4e81b4e7b01fp-1,0x1.1a2344551cc7ep-4},
  {0x1.af286bca2397cp-1,0x1.31b3055c23663p-4},
  {0x1.a98ef60696bd7p-1,0x1.48f3ed1e3553ap-4},
  {0x1.a41a41a40b6e9p-1,0x1.5fe80488ee159p-4},
  {0x1.9ec8e95113011p-1,0x1.769140a20ed4fp-4},
  {0x1.9999999992278p-1,0x1.8cf1838884d6ep-4},
  {0x1.948b0fcd69907p-1,0x1.a30a9d60b536dp-4},
  {0x1.8f9c18f9b4e11p-1,0x1.b8de4d3aec4d9p-4},
  {0x1.8acb90f6c68a1p-1,0x1.ce6e41e442e9ep-4},
  {0x1.861861862484bp-1,0x1.e3bc1ab0aa46fp-4},
  {0x1.8181818173ae4p-1,0x1.f8c96834a7e48p-4},
  {0x1.7d05f417c0774p-1,0x1.06cbd67a915cdp-3},
  {0x1.78a4c81784397p-1,0x1.11142f081f8dbp-3},
  {0x1.745d1745de7bap-1,0x1.1b3e71ec75d90p-3},
  {0x1.702e05c0c18e3p-1,0x1.254b4d35d0f5fp-3},
  {0x1.6c16c16c06c9bp-1,0x1.2f3b691c81020p-3},
  {0x1.681681681a1f6p-1,0x1.390f684497c90p-3},
  {0x1.642c8590aa5efp-1,0x1.42c7e7fe5304ep-3},
  {0x1.605816057fe92p-1,0x1.4c65807e96e6bp-3},
  {0x1.5c9882b922e81p-1,0x1.55e8c518d5130p-3},
  {0x1.58ed230825723p-1,0x1.5f5244722cd0fp-3},
  {0x1.555555555be48p-1,-0x1.ffbfc2bbe9af4p-4},
  {0x1.51d07eae25fe5p-1,-0x1.ed50a4a23c997p-4},
  {0x1.4e5e0a72ea8cap-1,-0x1.db11ed764c030p-4},
  {0x1.4afd6a051e80fp-1,-0x1.c902a19e1cbfdp-4},
  {0x1.47ae147ad26e5p-1,-0x1.b721cd16c4e1cp-4},
  {0x1.446f8656250cap-1,-0x1.a56e8325c6c50p-4},
  {0x1.4141414137fcep-1,-0x1.93e7de0f90978p-4},
  {0x1.3e22cbce5a6b7p-1,-0x1.828cfed2f2cafp-4},
  {0x1.3b13b13b04947p-1,-0x1.715d0ce3125d3p-4},
  {0x1.3813813803a55p-1,-0x1.605735ee3df92p-4},
  {0x1.3521cfb2b1096p-1,-0x1.4f7aad9b97441p-4},
  {0x1.323e34a2baef3p-1,-0x1.3ec6ad5440f5ap-4},
  {0x1.2f684bda0a126p-1,-0x1.2e3a740b43dffp-4},
  {0x1.2c9fb4d8060d5p-1,-0x1.1dd5460c3fb8bp-4},
  {0x1.29e4129e3187dp-1,-0x1.0d966cc5f2b50p-4},
  {0x1.27350b881d871p-1,-0x1.fafa6d3a047d3p-5},
  {0x1.249249249e9cfp-1,-0x1.db11ed7700a68p-5},
  {0x1.21fb781227951p-1,-0x1.bb7209d242ce5p-5},
  {0x1.1f7047dc1c36bp-1,-0x1.9c197abf7fb7ap-5},
  {0x1.1cf06ada1ee29p-1,-0x1.7d070145824fep-5},
  {0x1.1a7b961199740p-1,-0x1.5e3966b7356cbp-5},
  {0x1.181181181a457p-1,-0x1.3faf7c669fbf0p-5},
  {0x1.15b1e5f74ca8fp-1,-0x1.21681b5c42118p-5},
  {0x1.135c81135efedp-1,-0x1.0362241e83be8p-5},
  {0x1.1111111104099p-1,-0x1.cb38fccc387a4p-6},
  {0x1.0ecf56be75b6ep-1,-0x1.902c31d763f47p-6},
  {0x1.0c9714fbdd2a2p-1,-0x1.559bd2420786bp-6},
  {0x1.0a6810a68f2a2p-1,-0x1.1b85d605c7d78p-6},
  {0x1.084210841501dp-1,-0x1.c3d08374fd2e0p-7},
  {0x1.0624dd2f253e0p-1,-0x1.51824c77c8587p-7},
  {0x1.04104104183eep-1,-0x1.c03a80b1c8a49p-8},
  {0x1.0204081017ac2p-1,-0x1.be76bd7050239p-9},
  {0x1.0000000000000p-1,0x0.0p+0},
 };
 int exponent=std::bit_cast<int32_t>(word+0xc0c10000u)>>23;
 uint32_t mantissa=word&0x007fffffu;
 unsigned index=(mantissa+0x00010000u)>>17;
 double fraction=std::bit_cast<float>(mantissa|0x3f800000u);
 double residual=std::fma(fraction,table[index].reciprocal,-1.0);
 double polynomial=std::fma(residual,-0x1.bcbea4c258daap-4,0x1.287d3dcaf476dp-3);
 polynomial=std::fma(residual,polynomial,-0x1.bcb7b14dcbd85p-3);
 polynomial=std::fma(residual,polynomial,0x1.bcb7b151bc6b3p-2);
 double base=std::fma(static_cast<double>(exponent),0x1.34413509f79ffp-2,table[index].logarithm);
 return static_cast<float>(std::fma(residual,polynomial,base));
}
}
