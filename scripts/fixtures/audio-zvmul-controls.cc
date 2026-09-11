#if defined(__APPLE__)
#include <Accelerate/Accelerate.h>
#else
#include <xmmintrin.h>
#endif
#include <array>
#include <bit>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <vector>
int main(int argc,char**argv){
 if(argc!=2 && argc!=3)return 2;
#if defined(__aarch64__)
 uint64_t control;asm volatile("mrs %0, fpcr":"=r"(control));control|=uint64_t{1}<<24;asm volatile("msr fpcr, %0"::"r"(control));
#else
 _mm_setcsr(_mm_getcsr()|0x8040);
#endif
 const unsigned n=argc==3?20736:4096;
 std::vector<float> ar(n),ai(n),br(n),bi(n),rr(n),ri(n);
 uint32_t state=0x61bd734a;
 auto next=[&](){state^=state<<13;state^=state>>17;state^=state<<5;return (static_cast<int>(state&0xffff)-32768)/32768.f;};
 for(unsigned i=0;i<n;++i){ar[i]=next();ai[i]=next();br[i]=next();bi[i]=next();}
 if(argc==3){
  constexpr std::array<uint32_t,12> patterns={0,0x80000000,0x7f800000,0xff800000,0x7fc12345,0xffc23456,0x7f812345,0xff823456,0x7f7fffff,0xff7fffff,1,0x00800000};
  for(unsigned i=0;i<n;++i){ar[i]=std::bit_cast<float>(patterns[i%12]);ai[i]=std::bit_cast<float>(patterns[(i/12)%12]);br[i]=std::bit_cast<float>(patterns[(i/144)%12]);bi[i]=std::bit_cast<float>(patterns[(i/1728)%12]);}
 }
#if defined(__APPLE__)
 DSPSplitComplex a{ar.data(),ai.data()},b{br.data(),bi.data()},r{rr.data(),ri.data()};
 vDSP_zvmul(&a,1,&b,1,&r,1,n,1);
#else
 for(unsigned i=0;i<n;++i){rr[i]=ar[i]*br[i]-ai[i]*bi[i];ri[i]=ar[i]*bi[i]+ai[i]*br[i];}
#endif
 std::array<unsigned,4> count{};
 std::ofstream output(argv[1],std::ios::binary);
 for(unsigned i=0;i<n;++i){
  std::array<float,14> row={ar[i],ai[i],br[i],bi[i],rr[i],ri[i],
   ar[i]*br[i]-ai[i]*bi[i],ar[i]*bi[i]+ai[i]*br[i],
   std::fma(ar[i],br[i],-ai[i]*bi[i]),std::fma(ar[i],bi[i],ai[i]*br[i]),
   std::fma(-ai[i],bi[i],ar[i]*br[i]),std::fma(ai[i],br[i],ar[i]*bi[i]),
   static_cast<float>(double(ar[i])*br[i]-double(ai[i])*bi[i]),
   static_cast<float>(double(ar[i])*bi[i]+double(ai[i])*br[i])};
  for(unsigned mode=0;mode<4;++mode)for(unsigned part=0;part<2;++part)if(std::bit_cast<uint32_t>(row[4+part])!=std::bit_cast<uint32_t>(row[6+2*mode+part]))++count[mode];
  output.write(reinterpret_cast<const char*>(row.data()),sizeof(row));
 }
 std::printf("{\"complex_inputs\":%u,\"different_components_vs_vdsp\":[%u,%u,%u,%u],\"models\":[\"unfused\",\"first-product-fma\",\"second-product-fma\",\"double-rounded-once\"]}\n",n,count[0],count[1],count[2],count[3]);
}
