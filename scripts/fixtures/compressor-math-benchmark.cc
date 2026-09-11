#include <bit>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <algorithm>
#include <array>
#include <dlfcn.h>
#include "adapters.h"
#include "compressor-mac-arm-log10.h"
#include "compressor-mac-arm-exp10.h"
using Function=float(*)(float);
__attribute__((noinline)) float native_log(float x){return ::log10f(x);}
__attribute__((noinline)) float native_exp(float x){return ::powf(10.f,x);}
__attribute__((noinline)) float model_log(float x){return mac_arm_log10::Evaluate(x);}
__attribute__((noinline)) float model_exp(float x){return mac_arm_exp10::Evaluate(x);}
#if defined(__x86_64__)
__attribute__((noinline,target("fma"))) float fma_log(float x){return mac_arm_log10::Evaluate(x);}
__attribute__((noinline,target("fma"))) float fma_exp(float x){return mac_arm_exp10::Evaluate(x);}
#endif
int main(){
 EnableAudioFTZ();
 std::array<float,1024> logs,exps;
 uint32_t state=0x984137abu;
 for(unsigned i=0;i<logs.size();++i){state^=state<<13;state^=state>>17;state^=state<<5;logs[i]=0.001f+float(state&0xffff)/32768.f;exps[i]=-3.f+float((state>>16)&0xffff)/16384.f;}
 struct Test{const char*name;Function fn;const std::array<float,1024>*input;};
 std::vector<Test> tests={{"native_log10f",native_log,&logs},{"model_log10f",model_log,&logs},{"native_base10",native_exp,&exps},{"model_base10",model_exp,&exps}};
#if defined(__x86_64__)
 if(__builtin_cpu_supports("fma")){tests.push_back({"fma_model_log10f",fma_log,&logs});tests.push_back({"fma_model_base10",fma_exp,&exps});}
#endif
 std::puts("[");bool first=true;
 for(auto test:tests){std::array<double,5> times;uint32_t digest=0;
  for(unsigned batch=0;batch<5;++batch){auto start=std::chrono::steady_clock::now();for(unsigned r=0;r<1024;++r)for(float x:*test.input)digest+=std::bit_cast<uint32_t>(test.fn(x));times[batch]=std::chrono::duration<double,std::nano>(std::chrono::steady_clock::now()-start).count()/(1024*1024);}
  auto sorted=times;std::sort(sorted.begin(),sorted.end());std::printf("%s{\"name\":\"%s\",\"median_ns\":%.6f,\"digest\":%u,\"samples_ns\":[%.6f,%.6f,%.6f,%.6f,%.6f]}",first?"":",\n",test.name,sorted[2],digest,times[0],times[1],times[2],times[3],times[4]);first=false;
 }
 std::puts("\n]");
}
