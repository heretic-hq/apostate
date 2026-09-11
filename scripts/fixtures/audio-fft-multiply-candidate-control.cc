#include <array>
#include <bit>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <string>
#include <vector>
#include "mac-arm-fft-multiply.h"
#if defined(__APPLE__)
#include <Accelerate/Accelerate.h>
#endif
using namespace blink::mac_arm_fft_multiply;
int main(int argc,char**argv){
 if(argc!=5 && argc!=6 && argc!=7)return 2;
#if defined(ARCH_CPU_X86_FAMILY)
 _mm_setcsr(_mm_getcsr()|0x8040);
#else
 uint64_t fp;asm volatile("mrs %0, fpcr":"=r"(fp));fp|=uint64_t{1}<<24;asm volatile("msr fpcr, %0"::"r"(fp));
#endif
 std::ifstream input(argv[1],std::ios::binary);std::array<float,4> row;std::vector<float> ar,ai,br,bi;
 while(input.read(reinterpret_cast<char*>(row.data()),sizeof(row))){ar.push_back(row[0]);ai.push_back(row[1]);br.push_back(row[2]);bi.push_back(row[3]);}
 if(ar.empty())return 3;const auto n=ar.size();std::vector<float> real(n),imag(n);
 std::string mode=argv[3],alias=argv[4];
 base::span<float> r=alias=="first"||alias=="self"?base::span<float>(ar):alias=="second"?base::span<float>(br):base::span<float>(real);
 base::span<float> im=alias=="first"||alias=="self"?base::span<float>(ai):alias=="second"?base::span<float>(bi):base::span<float>(imag);
 base::span<const float> b_real=alias=="self"?base::span<const float>(ar):base::span<const float>(br);
 base::span<const float> b_imag=alias=="self"?base::span<const float>(ai):base::span<const float>(bi);
 const size_t step=argc>=6?std::stoul(argv[5]):n;
 if(!step)return 7;
 for(size_t offset=0;offset<n;offset+=step){
  const size_t count=std::min(step,n-offset);
  auto a_r=base::span<const float>(ar).subspan(offset,count);
  auto a_i=base::span<const float>(ai).subspan(offset,count);
  auto b_r=b_real.subspan(offset,count),b_i=b_imag.subspan(offset,count);
  auto out_r=r.subspan(offset,count),out_i=im.subspan(offset,count);
  const float saved_r=a_r[0],saved_i=a_i[0];
  if(mode=="native"){
#if defined(__APPLE__)
   DSPSplitComplex a{const_cast<float*>(a_r.data()),const_cast<float*>(a_i.data())},b{const_cast<float*>(b_r.data()),const_cast<float*>(b_i.data())},out{out_r.data(),out_i.data()};vDSP_zvmul(&a,1,&b,1,&out,1,count,1);
#else
   return 4;
#endif
  }else if(mode=="portable")Portable(a_r,a_i,b_r,b_i,out_r,out_i);
  else if(mode=="fma" || mode=="fast"){
#if defined(ARCH_CPU_X86_64)
   if(!__builtin_cpu_supports("fma")||!__builtin_cpu_supports("avx"))return 5;
   if(mode=="fast")Fast(a_r,a_i,b_r,b_i,out_r,out_i);else Fma(a_r,a_i,b_r,b_i,out_r,out_i);
#else
   return 5;
#endif
  }else return 6;
  if(argc==7 && std::string(argv[6])=="dc"){
   if(mode=="native"){out_r[0]=saved_r*b_r[0];out_i[0]=saved_i*b_i[0];}
   else{out_r[0]=MultiplyScalar(saved_r,b_r[0]);out_i[0]=MultiplyScalar(saved_i,b_i[0]);}
  }
 }
 std::ofstream output(argv[2],std::ios::binary);
 for(size_t i=0;i<n;++i){std::array<float,2> values={r[i],im[i]};output.write(reinterpret_cast<const char*>(values.data()),sizeof(values));}
}
