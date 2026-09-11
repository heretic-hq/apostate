#pragma once
#include <algorithm>
#include <array>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <initializer_list>
#include <memory>
#include <span>
#include <vector>
#include "third_party/fdlibm/ieee754.h"
#define PLATFORM_EXPORT
#define USING_FAST_MALLOC(...)
#define DCHECK(...) ((void)0)
#define DCHECK_EQ(...) ((void)0)
#define DCHECK_LT(...) ((void)0)
#define CHECK_EQ(a,b) assert((a)==(b))
constexpr float kPiOverTwoFloat = static_cast<float>(1.57079632679489661923);
template<class T> T ClampTo(T value, T low, T high) { return std::clamp(value,low,high); }
namespace base { template<class T> using span = std::span<T>; }
namespace blink {
template<class T> using Vector = std::vector<T>;
class AudioFloatArray {
 public:
  explicit AudioFloatArray(size_t size):data_(size){}
  void Zero(){std::fill(data_.begin(),data_.end(),0.f);}
  float& at(size_t i){return data_.at(i);}
 private:std::vector<float> data_;
};
class ChannelData {
 public:
  std::vector<float> data;
  explicit ChannelData(size_t n):data(n){}
  std::span<const float> Span() const{return data;}
  std::span<float> MutableSpan(){return data;}
};
class AudioBus {
 public:
  AudioBus(unsigned channels,unsigned frames){for(unsigned c=0;c<channels;++c)channels_.emplace_back(frames);}
  unsigned NumberOfChannels()const{return channels_.size();}
  const ChannelData* Channel(unsigned c)const{return &channels_[c];}
  ChannelData* Channel(unsigned c){return &channels_[c];}
 private:std::vector<ChannelData> channels_;
};
class DenormalDisabler {
 public:
  static float FlushDenormalFloatToZero(float f){return f;}
};
namespace audio_utilities {
float DecibelsToLinear(float);
float LinearToDecibels(float);
double DiscreteTimeConstantForSampleRate(double,double);
}
}
struct MathRecord { uint32_t operation; uint32_t frame; double a,b,result; };
inline uint32_t trace_frame = 0;
inline std::vector<MathRecord> math_records;
inline std::vector<float> stage_records;
inline float ObservedPowf(float a,float b){float r=::powf(a,b);math_records.push_back({1,trace_frame,a,b,r});return r;}
inline float ObservedLog10f(float a){float r=::log10f(a);math_records.push_back({2,trace_frame,a,0,r});return r;}
inline double ObservedExp(double a){double r=::exp(a);math_records.push_back({3,trace_frame,a,0,r});return r;}
inline double ObservedSin(double a){double r=::sin(a);math_records.push_back({4,trace_frame,a,0,r});return r;}
inline void RecordStages(std::initializer_list<float> values){stage_records.insert(stage_records.end(),values);++trace_frame;}
inline void EnableAudioFTZ(){
#if defined(__aarch64__)
 uint64_t csr;asm volatile("mrs %0, fpcr":"=r"(csr));csr|=1ull<<24;asm volatile("msr fpcr, %0"::"r"(csr));
#elif defined(__x86_64__)
 uint32_t csr;asm volatile("stmxcsr %0":"=m"(csr));csr|=0x8040;asm volatile("ldmxcsr %0"::"m"(csr));
#endif
}
