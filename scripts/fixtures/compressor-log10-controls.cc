#include <bit>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <vector>
#include "adapters.h"
int main(int argc,char**argv){
 if(argc!=3)return 2;
 EnableAudioFTZ();
 std::ifstream input(argv[1],std::ios::binary);
 std::ofstream output(argv[2],std::ios::binary);
 uint32_t word;
 while(input.read(reinterpret_cast<char*>(&word),4)){
  float x=std::bit_cast<float>(word);
  uint32_t result[]={word,std::bit_cast<uint32_t>(::log10f(x)),
    std::bit_cast<uint32_t>(static_cast<float>(fdlibm::log10(static_cast<double>(x)))),
    std::bit_cast<uint32_t>(static_cast<float>(::log10(static_cast<double>(x))))};
  output.write(reinterpret_cast<const char*>(result),sizeof(result));
 }
}
