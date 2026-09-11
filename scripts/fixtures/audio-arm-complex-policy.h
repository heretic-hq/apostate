#pragma once
#include <bit>
#include <cmath>
#include <cstdint>
#if defined(__x86_64__)
#include <xmmintrin.h>
#endif
namespace arm_complex_policy {
inline bool Nan(uint32_t x){return (x&0x7fffffffu)>0x7f800000u;}
inline bool Signaling(uint32_t x){return Nan(x)&&!(x&0x00400000u);}
inline bool FlushInputs(){
#if defined(__aarch64__)
 uint64_t value;asm volatile("mrs %0, fpcr":"=r"(value));return value&(uint64_t{1}<<24);
#elif defined(__x86_64__)
 return _mm_getcsr()&0x40;
#else
 return false;
#endif
}
inline float Mul(float a,float b){
 float result=a*b;if(!std::isnan(result))return result;
 uint32_t x=std::bit_cast<uint32_t>(a),y=std::bit_cast<uint32_t>(b),selected=0x7fc00000u;
 if(Signaling(x))selected=x;else if(Signaling(y))selected=y;else if(Nan(x))selected=x;else if(Nan(y))selected=y;
 return std::bit_cast<float>(selected|0x00400000u);
}
inline float Fma(float a,float b,float c){
 float result=std::fma(a,b,c);if(!std::isnan(result))return result;
 uint32_t x=std::bit_cast<uint32_t>(a),y=std::bit_cast<uint32_t>(b),z=std::bit_cast<uint32_t>(c);
 uint32_t selected=0x7fc00000u;
 if(Signaling(z))selected=z;else if(Signaling(x))selected=x;else if(Signaling(y))selected=y;
 else{
  const bool flush=FlushInputs();
  auto zero=[&](uint32_t w){w&=0x7fffffffu;return w==0||(flush&&w<0x00800000u);};
  auto inf=[](uint32_t w){return (w&0x7fffffffu)==0x7f800000u;};
  if(!((zero(x)&&inf(y))||(zero(y)&&inf(x)))){
   if(Nan(z))selected=z;else if(Nan(x))selected=x;else if(Nan(y))selected=y;
  }
 }
 return std::bit_cast<float>(selected|0x00400000u);
}
inline void Multiply(float ar,float ai,float br,float bi,float&real,float&imag){
 const float negative_ai=std::bit_cast<float>(std::bit_cast<uint32_t>(ai)^0x80000000u);
 real=Fma(negative_ai,bi,Mul(ar,br));imag=Fma(ai,br,Mul(ar,bi));
}
}
