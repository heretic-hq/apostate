#include <algorithm>
#include <cmath>
#include <fstream>
#include <string>
#include <vector>
#include <cstdint>
#if defined(__x86_64__)
#include <xmmintrin.h>
#endif
#if defined(__APPLE__)
#include <Accelerate/Accelerate.h>
#endif
std::vector<float> Read(const char*path){std::ifstream f(path,std::ios::binary|std::ios::ate);if(!f)throw 1;auto size=f.tellg();f.seekg(0);std::vector<float> x(size/4);f.read(reinterpret_cast<char*>(x.data()),size);return x;}
void Write(const std::string&path,const float*real,const float*imag,unsigned n){std::ofstream f(path,std::ios::binary);f.write(reinterpret_cast<const char*>(real),n*4);f.write(reinterpret_cast<const char*>(imag),n*4);}
int main(int argc,char**argv){
 if(argc!=6)return 2;
#if defined(__aarch64__)
 uint64_t fp;asm volatile("mrs %0, fpcr":"=r"(fp));fp|=uint64_t{1}<<24;asm volatile("msr fpcr, %0"::"r"(fp));
#elif defined(__x86_64__)
 _mm_setcsr(_mm_getcsr()|0x8040);
#endif
 auto a=Read(argv[1]),b=Read(argv[2]);if(a.size()!=b.size()||a.size()%2)return 3;
 unsigned n=a.size()/2;std::string mode=argv[4],alias=argv[5];std::vector<float> output(a.size());
 float* ar=a.data();float* ai=a.data()+n;float* br=alias=="self"?ar:b.data();float* bi=alias=="self"?ai:b.data()+n;
 float* rr=alias=="first"||alias=="self"?ar:alias=="second"?br:output.data();
 float* ri=alias=="first"||alias=="self"?ai:alias=="second"?bi:output.data()+n;
 float real0=ar[0],imag0=ai[0];
 if(mode=="native"){
#if defined(__APPLE__)
  DSPSplitComplex x{ar,ai},y{br,bi},z{rr,ri};vDSP_zvmul(&x,1,&y,1,&z,1,n,1);
#else
  for(unsigned i=0;i<n;++i){float r1=ar[i],i1=ai[i],r2=br[i],i2=bi[i];rr[i]=r1*r2-i1*i2;ri[i]=r1*i2+i1*r2;}
#endif
 }else{
  if(mode!="fma")return 4;
  for(unsigned i=0;i<n;++i){float r1=ar[i],i1=ai[i],r2=br[i],i2=bi[i];rr[i]=std::fma(-i1,i2,r1*r2);ri[i]=std::fma(i1,r2,r1*i2);}
 }
 Write(std::string(argv[3])+".complex",rr,ri,n);
 // Preserve FFTFrame::Multiply's original post-product DC/Nyquist order.
 rr[0]=real0*br[0];ri[0]=imag0*bi[0];
 Write(argv[3],rr,ri,n);
}
