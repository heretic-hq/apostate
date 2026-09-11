#include <array>
#include <bit>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>
int main(int argc,char**argv){
 if(argc!=3)return 2;
 const std::filesystem::path tables=argv[1],out=argv[2];
 const float sample_rate=48000.f,frequency=997.f;
 const unsigned size=4096;const float lowest=sample_rate/size;
 const float rate_scale=size/sample_rate;
 const float phase=frequency*rate_scale;
 const float recovered=(1.f/rate_scale)*phase;
 for(int mode=0;mode<3;++mode){
  const float ratio=mode?recovered*(1.f/lowest):recovered/lowest;
  const float cents=::log2f(ratio)*1200.f;
  const float range=1.f+(mode?cents*(1.f/400.f):cents/400.f);
  const unsigned hi=static_cast<unsigned>(range),lo=hi+1;
  const float table_factor=range-hi;
  std::array<float,4096> lower,higher;
  for(auto pair:{std::pair{lo,&lower},std::pair{hi,&higher}}){std::ifstream f(tables/("range_"+std::to_string(pair.first)+".normalized.f32"),std::ios::binary);f.read(reinterpret_cast<char*>(pair.second->data()),4096*4);if(!f)return 3;}
  std::vector<float> output;
  for(unsigned n=0;n<128;++n){
   double d=double(n)*phase;d-=std::floor(d/size)*size;
   float f=d;
   if(mode)f-=std::floor(f/size)*size;
   unsigned i=static_cast<unsigned>(mode?double(f):d)&(size-1),j=(i+1)&(size-1);
   float mix=f-i;
   float h=mode==2?std::fma(mix,higher[j]-higher[i],higher[i]):higher[i]+mix*(higher[j]-higher[i]);
   float l=mode==2?std::fma(mix,lower[j]-lower[i],lower[i]):lower[i]+mix*(lower[j]-lower[i]);
   output.push_back(mode==2?std::fma(table_factor,l-h,h):h+table_factor*(l-h));
  }
  std::ofstream f(out/("mode"+std::to_string(mode)+".f32"),std::ios::binary);f.write(reinterpret_cast<char*>(output.data()),output.size()*4);
  std::printf("mode%d phase=%a recovered=%a ratio=%a cents=%a range=%a factor=%a tables=%u,%u\n",mode,phase,recovered,ratio,cents,range,table_factor,hi,lo);
 }
}
