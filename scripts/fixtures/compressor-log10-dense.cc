#include <bit>
#include <cstdio>
#include <cstdint>
#include <algorithm>
#include "adapters.h"
int main(){
 struct Range{const char*name;uint32_t center;};
 const Range ranges[]={{"observed_envelope",0x3f8886ac},{"actual_argument_counterexample",0x3f10c400},
  {"unity",0x3f800000},{"minimum_normal",0x00800000},{"zero",0},
  {"minimum_threshold_linear",0x3727c5ac},{"maximum_finite",0x7f7fffff}};
 std::printf("{\"diagnostic_only\":true,\"radius_ulps\":65536,\"ranges\":[");
 bool first=true;
 for(int mode=0;mode<2;++mode){
  if(mode)EnableAudioFTZ();
  for(auto range:ranges){
   uint32_t low=range.center>65536?range.center-65536:0;
   uint32_t high=std::min(uint64_t(range.center)+65536,uint64_t(0x7f800000));
   uint64_t differences=0;uint32_t first_input=0,first_native=0,first_candidate=0;
   for(uint64_t bits=low;bits<=high;++bits){
    float x=std::bit_cast<float>(static_cast<uint32_t>(bits));
    uint32_t native=std::bit_cast<uint32_t>(::log10f(x));
    uint32_t candidate=std::bit_cast<uint32_t>(static_cast<float>(fdlibm::log10(static_cast<double>(x))));
    if(native!=candidate){if(!differences){first_input=bits;first_native=native;first_candidate=candidate;}++differences;}
   }
   std::printf("%s{\"name\":\"%s\",\"ftz\":%s,\"low_bits\":%u,\"high_bits\":%u,\"inputs\":%llu,\"differences\":%llu,\"first_input\":%u,\"first_native\":%u,\"first_candidate\":%u}",first?"":",",range.name,mode?"true":"false",low,high,static_cast<unsigned long long>(high)-low+1,static_cast<unsigned long long>(differences),first_input,first_native,first_candidate);first=false;
  }
 }
 std::printf("]}\n");
}
