#include <bit>
#include <cstdint>
#include <fstream>
#include <dlfcn.h>
#include "adapters.h"
#include "compressor-mac-arm-exp10.h"
int main(int argc,char**argv){
 if(argc!=4)return 2;
 if(argv[3][0]=='1')EnableAudioFTZ();
 auto native=reinterpret_cast<float(*)(float)>(dlsym(RTLD_DEFAULT,"__exp10f"));
#if defined(__APPLE__)
 if(!native)return 3;
#endif
 std::ifstream input(argv[1],std::ios::binary);std::ofstream output(argv[2],std::ios::binary);
 uint32_t word;
 while(input.read(reinterpret_cast<char*>(&word),4)){
  float x=std::bit_cast<float>(word);
  uint32_t result[]={word,std::bit_cast<uint32_t>(native ? native(x) : ::powf(10.f,x)),std::bit_cast<uint32_t>(mac_arm_exp10::Evaluate(x))};
  output.write(reinterpret_cast<const char*>(result),sizeof(result));
 }
}
