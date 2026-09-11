#include <cstdlib>
#include <filesystem>
#include <string>
int main(int argc,char**argv){
 assert(argc==11);
 const std::filesystem::path input_path=argv[1], output_dir=argv[2];
 unsigned frames=std::stoul(argv[3]),channels=std::stoul(argv[4]);
 float sample_rate=std::stof(argv[5]);
 EnableAudioFTZ();
 std::vector<float> input(frames*channels),output(frames*2);
 std::ifstream input_file(input_path,std::ios::binary);input_file.read(reinterpret_cast<char*>(input.data()),input.size()*4);assert(input_file.gcount()==static_cast<std::streamsize>(input.size()*4));
 blink::DynamicsCompressor compressor(sample_rate,2);
 for(unsigned i=0;i<5;++i)compressor.SetParameterValue(i,std::stof(argv[6+i]));
 for(unsigned offset=0;offset<frames;offset+=128){
  blink::AudioBus source(channels,128),destination(2,128);
  unsigned count=std::min(128u,frames-offset);
  for(unsigned c=0;c<channels;++c)std::copy_n(input.begin()+c*frames+offset,count,source.Channel(c)->data.begin());
  compressor.Process(&source,&destination,128);
  for(unsigned c=0;c<2;++c)std::copy_n(destination.Channel(c)->data.begin(),count,output.begin()+c*frames+offset);
 }
 auto dump=[&](const char*name,const void*data,size_t bytes){std::ofstream f(output_dir/name,std::ios::binary);f.write(static_cast<const char*>(data),bytes);};
 dump("output.f32",output.data(),output.size()*4);
 dump("stages.f32",stage_records.data(),stage_records.size()*4);
 dump("math.bin",math_records.data(),math_records.size()*sizeof(MathRecord));
}
