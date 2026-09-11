// Standalone diagnostic harness. No browser integration or profile behavior.
#include <algorithm>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <limits>
#include <string>
#include <vector>
#include "BarcodeFormat.h"
#include "ReadBarcode.h"

std::string JsonString(const std::string& value) {
  std::string result="\"";
  const char* hex="0123456789abcdef";
  for(unsigned char c:value) {
    if(c=='"'||c=='\\') {result+='\\';result+=c;}
    else if(c<32) {result+="\\u00";result+=hex[c>>4];result+=hex[c&15];}
    else result+=c;
  }
  return result+'"';
}

int main(int argc,char** argv) {
  try {
    if(argc!=5)throw std::runtime_error("usage: reader gray-file width height formats");
    int width=std::stoi(argv[2]),height=std::stoi(argv[3]);
    if(width<=0||height<=0||width>4096||height>4096)throw std::runtime_error("control image size out of range");
    std::ifstream stream(argv[1],std::ios::binary);
    std::vector<uint8_t> pixels((std::istreambuf_iterator<char>(stream)),{});
    if(pixels.size()!=static_cast<size_t>(width)*height)throw std::runtime_error("control pixel size mismatch");
    auto options=ZXing::ReaderOptions().setTextMode(ZXing::TextMode::Plain)
      .setFormats(ZXing::BarcodeFormatsFromString(argv[4]));
    auto results=ZXing::ReadBarcodes(ZXing::ImageView(pixels.data(),width,height,ZXing::ImageFormat::Lum),options);
    std::cout<<std::setprecision(17)<<"[";bool first=true;
    for(const auto& barcode:results) {
      if(!first)std::cout<<",";first=false;
      int minx=std::numeric_limits<int>::max(),miny=minx,maxx=std::numeric_limits<int>::min(),maxy=maxx;
      for(const auto& p:barcode.position()){minx=std::min(minx,p.x);miny=std::min(miny,p.y);maxx=std::max(maxx,p.x);maxy=std::max(maxy,p.y);}
      std::cout<<"{\"rawValue\":"<<JsonString(barcode.text())<<",\"zxingFormat\":"<<JsonString(ZXing::ToString(barcode.format()))
        <<",\"boundingBox\":{\"x\":"<<minx<<",\"y\":"<<miny<<",\"width\":"<<maxx-minx<<",\"height\":"<<maxy-miny<<"},\"cornerPoints\":[";
      bool first_point=true;for(const auto& p:barcode.position()){if(!first_point)std::cout<<",";first_point=false;std::cout<<"{\"x\":"<<p.x<<",\"y\":"<<p.y<<"}";}
      std::cout<<"]}";
    }
    std::cout<<"]\n";return 0;
  }catch(const std::exception& error){std::cerr<<error.what()<<"\n";return 1;}
}
