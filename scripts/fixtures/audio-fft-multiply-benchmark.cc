#include <array>
#include <algorithm>
#include <bit>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <vector>
#include "mac-arm-fft-multiply.h"
#if defined(__APPLE__)
#include <Accelerate/Accelerate.h>
#endif
using namespace blink::mac_arm_fft_multiply;
__attribute__((noinline)) void Native(base::span<const float> ar,base::span<const float> ai,base::span<const float> br,base::span<const float> bi,base::span<float> r,base::span<float> im){
#if defined(__APPLE__)
 DSPSplitComplex a{const_cast<float*>(ar.data()),const_cast<float*>(ai.data())},b{const_cast<float*>(br.data()),const_cast<float*>(bi.data())},o{r.data(),im.data()};vDSP_zvmul(&a,1,&b,1,&o,1,ar.size(),1);
#else
 for(size_t i=0;i<ar.size();++i){r[i]=ar[i]*br[i]-ai[i]*bi[i];im[i]=ar[i]*bi[i]+ai[i]*br[i];}
#endif
}
__attribute__((noinline)) void Call(Function fn,base::span<const float> ar,base::span<const float> ai,base::span<const float> br,base::span<const float> bi,base::span<float> r,base::span<float> im){fn(ar,ai,br,bi,r,im);}
int main(int argc,char**argv){
 if(argc!=2)return 2;
#if defined(ARCH_CPU_X86_FAMILY)
 _mm_setcsr(_mm_getcsr()|0x8040);
#else
 uint64_t fp;asm volatile("mrs %0, fpcr":"=r"(fp));fp|=uint64_t{1}<<24;asm volatile("msr fpcr, %0"::"r"(fp));
#endif
 std::ifstream input(argv[1],std::ios::binary);std::vector<float> ar,ai,br,bi;std::array<float,4> row;
 while(ar.size()<512&&input.read(reinterpret_cast<char*>(row.data()),sizeof(row))){ar.push_back(row[0]);ai.push_back(row[1]);br.push_back(row[2]);bi.push_back(row[3]);}if(ar.size()!=512)return 3;
 std::vector<float> real(512),imag(512);struct Test{const char*name;Function fn;};std::vector<Test> tests={{"native",Native},{"portable",Portable}};
#if defined(ARCH_CPU_X86_64)
 if(__builtin_cpu_supports("avx")&&__builtin_cpu_supports("fma")){tests.push_back({"fma",Fma});tests.push_back({"fast",Fast});}
#endif
 std::puts("[");bool first=true;for(auto test:tests){std::array<double,5> times;uint32_t digest=0;
 for(unsigned batch=0;batch<5;++batch){auto begin=std::chrono::steady_clock::now();for(unsigned k=0;k<8192;++k){ar[0]=std::bit_cast<float>(std::bit_cast<uint32_t>(ar[0])^0x80000000u);Call(test.fn,ar,ai,br,bi,real,imag);digest+=std::bit_cast<uint32_t>(real[k%512]);}times[batch]=std::chrono::duration<double,std::nano>(std::chrono::steady_clock::now()-begin).count()/8192.;}
 auto sorted=times;std::sort(sorted.begin(),sorted.end());std::printf("%s{\"name\":\"%s\",\"complex_per_call\":512,\"median_ns\":%.6f,\"digest\":%u,\"samples\":[%.6f,%.6f,%.6f,%.6f,%.6f]}",first?"":",\n",test.name,sorted[2],digest,times[0],times[1],times[2],times[3],times[4]);first=false;}
 std::puts("\n]");
}
