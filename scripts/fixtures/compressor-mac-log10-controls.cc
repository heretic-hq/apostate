#include <bit>
#include <cstdint>
#include <fstream>
#include "adapters.h"
#include "compressor-mac-arm-log10.h"
int main(int argc,char**argv){
 if(argc!=4)return 2;
 if(argv[3][0]=='1')EnableAudioFTZ();
 std::ifstream input(argv[1],std::ios::binary);
 std::ofstream output(argv[2],std::ios::binary);
 uint32_t word;
 while(input.read(reinterpret_cast<char*>(&word),4)){
  float x=std::bit_cast<float>(word);
  uint32_t result[]={word,std::bit_cast<uint32_t>(::log10f(x)),std::bit_cast<uint32_t>(mac_arm_log10::Evaluate(x))};
  output.write(reinterpret_cast<const char*>(result),sizeof(result));
 }
}
