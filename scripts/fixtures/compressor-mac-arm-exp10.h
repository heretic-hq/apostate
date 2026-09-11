// Diagnostic arithmetic model of the measured macOS ARM __exp10f routine.
// Chromium's powf(10,x) call lowers to this base-ten exponential on Mac.
// The128 numeric constants reduce every exponent to a fractional power of2;
// they are not captured outputs indexed by audio samples or test cases.
#pragma once
#include <bit>
#include <cmath>
#include <cfenv>
#include <cstdint>
#include <limits>
namespace mac_arm_exp10 {
inline float Evaluate(float value){
 uint32_t word=std::bit_cast<uint32_t>(value);
 uint32_t magnitude=word&0x7fffffffu;
 if(magnitude>=0x42800000u){
  if((word&0x80000000u) && magnitude<=0x7f800000u){
   if(magnitude!=0x7f800000u)std::feraiseexcept(FE_UNDERFLOW|FE_INEXACT);
   return 0.f;
  }
  return value*std::numeric_limits<float>::max();
 }
 static constexpr double table[]={
  0x1.0000000000000p+0,
  0x1.0163da9fb3335p+0,
  0x1.02c9a3e778061p+0,
  0x1.04315e86e7f85p+0,
  0x1.059b0d3158574p+0,
  0x1.0706b29ddf6dep+0,
  0x1.0874518759bc8p+0,
  0x1.09e3ecac6f383p+0,
  0x1.0b5586cf9890fp+0,
  0x1.0cc922b7247f7p+0,
  0x1.0e3ec32d3d1a2p+0,
  0x1.0fb66affed31bp+0,
  0x1.11301d0125b51p+0,
  0x1.12abdc06c31ccp+0,
  0x1.1429aaea92de0p+0,
  0x1.15a98c8a58e51p+0,
  0x1.172b83c7d517bp+0,
  0x1.18af9388c8deap+0,
  0x1.1a35beb6fcb75p+0,
  0x1.1bbe084045cd4p+0,
  0x1.1d4873168b9aap+0,
  0x1.1ed5022fcd91dp+0,
  0x1.2063b88628cd6p+0,
  0x1.21f49917ddc96p+0,
  0x1.2387a6e756238p+0,
  0x1.251ce4fb2a63fp+0,
  0x1.26b4565e27cddp+0,
  0x1.284dfe1f56381p+0,
  0x1.29e9df51fdee1p+0,
  0x1.2b87fd0dad990p+0,
  0x1.2d285a6e4030bp+0,
  0x1.2ecafa93e2f56p+0,
  0x1.306fe0a31b715p+0,
  0x1.32170fc4cd831p+0,
  0x1.33c08b26416ffp+0,
  0x1.356c55f929ff1p+0,
  0x1.371a7373aa9cbp+0,
  0x1.38cae6d05d866p+0,
  0x1.3a7db34e59ff7p+0,
  0x1.3c32dc313a8e5p+0,
  0x1.3dea64c123422p+0,
  0x1.3fa4504ac801cp+0,
  0x1.4160a21f72e2ap+0,
  0x1.431f5d950a897p+0,
  0x1.44e086061892dp+0,
  0x1.46a41ed1d0057p+0,
  0x1.486a2b5c13cd0p+0,
  0x1.4a32af0d7d3dep+0,
  0x1.4bfdad5362a27p+0,
  0x1.4dcb299fddd0dp+0,
  0x1.4f9b2769d2ca7p+0,
  0x1.516daa2cf6642p+0,
  0x1.5342b569d4f82p+0,
  0x1.551a4ca5d920fp+0,
  0x1.56f4736b527dap+0,
  0x1.58d12d497c7fdp+0,
  0x1.5ab07dd485429p+0,
  0x1.5c9268a5946b7p+0,
  0x1.5e76f15ad2148p+0,
  0x1.605e1b976dc09p+0,
  0x1.6247eb03a5585p+0,
  0x1.6434634ccc320p+0,
  0x1.6623882552225p+0,
  0x1.68155d44ca973p+0,
  0x1.6a09e667f3bcdp+0,
  0x1.6c012750bdabfp+0,
  0x1.6dfb23c651a2fp+0,
  0x1.6ff7df9519484p+0,
  0x1.71f75e8ec5f74p+0,
  0x1.73f9a48a58174p+0,
  0x1.75feb564267c9p+0,
  0x1.780694fde5d3fp+0,
  0x1.7a11473eb0187p+0,
  0x1.7c1ed0130c132p+0,
  0x1.7e2f336cf4e62p+0,
  0x1.80427543e1a12p+0,
  0x1.82589994cce13p+0,
  0x1.8471a4623c7adp+0,
  0x1.868d99b4492edp+0,
  0x1.88ac7d98a6699p+0,
  0x1.8ace5422aa0dbp+0,
  0x1.8cf3216b5448cp+0,
  0x1.8f1ae99157736p+0,
  0x1.9145b0b91ffc6p+0,
  0x1.93737b0cdc5e5p+0,
  0x1.95a44cbc8520fp+0,
  0x1.97d829fde4e50p+0,
  0x1.9a0f170ca07bap+0,
  0x1.9c49182a3f090p+0,
  0x1.9e86319e32323p+0,
  0x1.a0c667b5de565p+0,
  0x1.a309bec4a2d33p+0,
  0x1.a5503b23e255dp+0,
  0x1.a799e1330b358p+0,
  0x1.a9e6b5579fdbfp+0,
  0x1.ac36bbfd3f37ap+0,
  0x1.ae89f995ad3adp+0,
  0x1.b0e07298db666p+0,
  0x1.b33a2b84f15fbp+0,
  0x1.b59728de5593ap+0,
  0x1.b7f76f2fb5e47p+0,
  0x1.ba5b030a1064ap+0,
  0x1.bcc1e904bc1d2p+0,
  0x1.bf2c25bd71e09p+0,
  0x1.c199bdd85529cp+0,
  0x1.c40ab5fffd07ap+0,
  0x1.c67f12e57d14bp+0,
  0x1.c8f6d9406e7b5p+0,
  0x1.cb720dcef9069p+0,
  0x1.cdf0b555dc3fap+0,
  0x1.d072d4a07897cp+0,
  0x1.d2f87080d89f2p+0,
  0x1.d5818dcfba487p+0,
  0x1.d80e316c98398p+0,
  0x1.da9e603db3285p+0,
  0x1.dd321f301b460p+0,
  0x1.dfc97337b9b5fp+0,
  0x1.e264614f5a129p+0,
  0x1.e502ee78b3ff6p+0,
  0x1.e7a51fbc74c83p+0,
  0x1.ea4afa2a490dap+0,
  0x1.ecf482d8e67f1p+0,
  0x1.efa1bee615a27p+0,
  0x1.f252b376bba97p+0,
  0x1.f50765b6e4540p+0,
  0x1.f7bfdad9cbe14p+0,
  0x1.fa7c1819e90d8p+0,
  0x1.fd3c22b8f71f1p+0,
 };
 double scaled=static_cast<double>(value)*0x1.a934f0979a371p+8;
 // FRINTA rounds to nearest with ties away, independent of ambient mode.
 double rounded=std::round(scaled);
 int64_t n=static_cast<int64_t>(rounded);
 double residual=scaled-rounded;
 unsigned index=static_cast<uint64_t>(n)&127u;
 int64_t exponent=n>>7;
 uint64_t bits=std::bit_cast<uint64_t>(table[index])+
               (static_cast<uint64_t>(exponent)<<52);
 double power=std::bit_cast<double>(bits);
 double polynomial=std::fma(0x1.ebfbdff30d656p-17,residual,0x1.62e4453e10daep-8);
 double adjustment=polynomial*residual;
 return static_cast<float>(std::fma(adjustment,power,power));
}
}
