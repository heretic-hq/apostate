#include <dlfcn.h>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <string>
int main(int argc,char**argv){
 if(argc!=2 && argc!=3)return 2;
 void* symbol=dlsym(RTLD_DEFAULT,argc==3?argv[2]:"log10f");
 Dl_info info{};
 if(!symbol||!dladdr(symbol,&info))return 3;
 std::printf("symbol=%p image=%s base=%p name=%s\n",symbol,info.dli_fname,info.dli_fbase,info.dli_sname);
 FILE* file=std::fopen(argv[1],"wb");if(!file)return 4;
 std::fwrite(symbol,1,1312,file);std::fclose(file);
 if(argc==3 && std::strcmp(argv[2],"__exp10f")==0){
  auto* bytes=static_cast<unsigned char*>(symbol);
  uint32_t adrp,add;std::memcpy(&adrp,bytes+0x30,4);std::memcpy(&add,bytes+0x34,4);
  int32_t pages=static_cast<int32_t>((((adrp>>5)&0x7ffff)<<2)|((adrp>>29)&3));
  if(pages&(1<<20))pages-=1<<21;
  uintptr_t base=(reinterpret_cast<uintptr_t>(symbol)+0x30)&~uintptr_t(4095);
  uintptr_t table=base+static_cast<int64_t>(pages)*4096+((add>>10)&0xfff);
  std::printf("table=%p pages=%d add=%u\n",reinterpret_cast<void*>(table),pages,(add>>10)&0xfff);
  std::string path=std::string(argv[1])+".table";
  FILE* data=std::fopen(path.c_str(),"wb");if(!data)return 5;
  std::fwrite(reinterpret_cast<void*>(table-16),1,16+128*8,data);std::fclose(data);
 }
}
