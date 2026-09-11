#include "audio-arm-complex-policy.h"
#include <array>
#include <fstream>
int main(int argc,char**argv){
 if(argc!=2)return 2;
#if defined(__x86_64__)
 _mm_setcsr(_mm_getcsr()|0x8040);
#endif
 constexpr std::array<uint32_t,12> patterns={0,0x80000000,0x7f800000,0xff800000,0x7fc12345,0xffc23456,0x7f812345,0xff823456,0x7f7fffff,0xff7fffff,1,0x00800000};
 std::ofstream output(argv[1],std::ios::binary);
 for(unsigned i=0;i<20736;++i){float ar=std::bit_cast<float>(patterns[i%12]),ai=std::bit_cast<float>(patterns[(i/12)%12]),br=std::bit_cast<float>(patterns[(i/144)%12]),bi=std::bit_cast<float>(patterns[(i/1728)%12]);std::array<float,2> result;arm_complex_policy::Multiply(ar,ai,br,bi,result[0],result[1]);output.write(reinterpret_cast<const char*>(result.data()),sizeof(result));}
}
