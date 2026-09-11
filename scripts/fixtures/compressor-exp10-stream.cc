#include <bit>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include "adapters.h"
#include "compressor-mac-arm-exp10.h"
#include <dlfcn.h>
int main(int argc,char**argv){
 if(argc!=4)return 2;
 auto native=reinterpret_cast<float(*)(float)>(dlsym(RTLD_DEFAULT,"__exp10f"));
 if(!native)return 3;
 uint32_t low=std::strtoul(argv[1],nullptr,0),high=std::strtoul(argv[2],nullptr,0);
 if(argv[3][0]=='1')EnableAudioFTZ();
 uint64_t fp_control=0;
#if defined(__aarch64__)
 asm volatile("mrs %0, fpcr":"=r"(fp_control));
#elif defined(__x86_64__)
 uint32_t mxcsr;asm volatile("stmxcsr %0":"=m"(mxcsr));fp_control=mxcsr;
#endif
 int rounding=std::fegetround();
 uint64_t count=0,different=0;uint32_t first=0,left=0,right=0;
 auto start=std::chrono::steady_clock::now();
 for(uint64_t word=low;word<=high;++word){
  float x=std::bit_cast<float>(static_cast<uint32_t>(word));
  uint32_t a=std::bit_cast<uint32_t>(native(x)),b=std::bit_cast<uint32_t>(mac_arm_exp10::Evaluate(x));
  if(a!=b){if(!different){first=word;left=a;right=b;}++different;}++count;
 }
 double seconds=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
 std::printf("{\"low\":%u,\"high\":%u,\"ftz\":%s,\"fp_control\":%llu,\"rounding_mode\":%d,\"count\":%llu,\"different\":%llu,\"first_input\":%u,\"first_native\":%u,\"first_candidate\":%u,\"seconds\":%.9f}\n",low,high,argv[3][0]=='1'?"true":"false",static_cast<unsigned long long>(fp_control),rounding,static_cast<unsigned long long>(count),static_cast<unsigned long long>(different),first,left,right,seconds);
}
