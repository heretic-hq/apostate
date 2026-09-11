// Standalone diagnostic adapters around pinned PeriodicWave method bodies.
// The generator inserts those bodies unchanged except for a stage-dump hook.
#include <algorithm>
#include <cassert>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <memory>
#include <numbers>
#include <span>
#include <stdexcept>
#include <string>
#include <vector>
#ifdef __APPLE__
#include <Accelerate/Accelerate.h>
#endif

namespace fs = std::filesystem;
namespace base { template<class T> using span = std::span<T>; }
namespace v8 { struct Isolate { static void* GetCurrent() { return nullptr; } }; }
#define DCHECK_GE(a,b) assert((a) >= (b))
#define NOTREACHED() throw std::runtime_error("unreachable shape")
constexpr float kPiFloat = std::numbers::pi_v<float>;
constexpr unsigned kNumberOfOctaveBands = 3;
constexpr unsigned kMaxPeriodicWaveSize = 16384;
constexpr float kCentsPerRange = 1200 / kNumberOfOctaveBands;

fs::path case_directory;
bool finalize_tables = false;
unsigned current_range = 0;

void WriteFloats(const fs::path& path, base::span<const float> data) {
  std::ofstream out(path, std::ios::binary);
  out.write(reinterpret_cast<const char*>(data.data()), data.size_bytes());
  if (!out) throw std::runtime_error("cannot write " + path.string());
}
void ReadFloats(const fs::path& path, base::span<float> data) {
  if (fs::file_size(path) != data.size_bytes()) throw std::runtime_error("wrong PCM size");
  std::ifstream in(path, std::ios::binary);
  in.read(reinterpret_cast<char*>(data.data()), data.size_bytes());
  if (!in) throw std::runtime_error("cannot read " + path.string());
}
std::string RangePrefix(unsigned index) {
  return "range_" + std::to_string(index);
}

class AudioFloatArray {
 public:
  explicit AudioFloatArray(unsigned length = 0) : values_(length) {}
  void Allocate(unsigned length) { values_.assign(length, 0.0f); }
  bool TryAllocate(unsigned length) { Allocate(length); return true; }
  size_t size() const { return values_.size(); }
  float& operator[](size_t index) { return values_.at(index); }
  base::span<float> as_span() { return values_; }
 private:
  std::vector<float> values_;
};

namespace vector_math {
void Vsmul(base::span<const float> source, float scale, base::span<float> dest, size_t size) {
#ifdef __APPLE__
  vDSP_vsmul(source.data(), 1, &scale, dest.data(), 1, size);
#else
  for (size_t i = 0; i < size; ++i) dest[i] = source[i] * scale;
#endif
}
float Vmaxmgv(base::span<const float> source, size_t size) {
  float value = 0;
#ifdef __APPLE__
  vDSP_maxmgv(source.data(), 1, &value, size);
#else
  for (size_t i = 0; i < size; ++i) value = std::max(value, std::fabs(source[i]));
#endif
  return value;
}
}

class FFTFrame {
 public:
  explicit FFTFrame(unsigned size) : real_(size / 2), imag_(size / 2), range_(0) {}
  AudioFloatArray& RealData() { return real_; }
  AudioFloatArray& ImagData() { return imag_; }
  void DoInverseFFT(base::span<float> data) {
    current_range = range_++;
    const auto prefix = RangePrefix(current_range);
    if (finalize_tables) {
      ReadFloats(case_directory / (prefix + ".ifft.f32"), data);
    } else {
      WriteFloats(case_directory / (prefix + ".real.f32"), real_.as_span());
      WriteFloats(case_directory / (prefix + ".imag.f32"), imag_.as_span());
      std::fill(data.begin(), data.end(), 0.0f);
    }
  }
 private:
  AudioFloatArray real_, imag_;
  unsigned range_;
};

struct OscillatorHandler { enum { SINE, SQUARE, SAWTOOTH, TRIANGLE }; };
class PeriodicWaveImpl {
 public:
  explicit PeriodicWaveImpl(float sample_rate);
  unsigned PeriodicWaveSize() const;
  unsigned MaxNumberOfPartials() const;
  unsigned NumberOfRanges() const { return number_of_ranges_; }
  unsigned NumberOfPartialsForRange(unsigned range_index) const;
  bool CreateBandLimitedTables(base::span<const float> real, base::span<const float> imag,
                               bool disable_normalization);
  bool GenerateBasicWaveform(int shape);
 private:
  float sample_rate_, cents_per_range_, lowest_fundamental_frequency_, rate_scale_;
  unsigned number_of_ranges_;
  std::vector<std::unique_ptr<AudioFloatArray>> band_limited_tables_;
  struct Accounter { void Increase(void*, size_t) {} } external_memory_accounter_;
};

// GENERATED_PERIODIC_WAVE_METHODS

int main(int argc, char** argv) {
  if (argc != 3) return 2;
  finalize_tables = std::string(argv[1]) == "finalize";
  struct Case { const char* name; float rate; int shape; };
  const Case cases[] = {
    {"triangle-44100", 44100, OscillatorHandler::TRIANGLE},
    {"saw-24000-heldout", 24000, OscillatorHandler::SAWTOOTH},
    {"sine-48000-heldout", 48000, OscillatorHandler::SINE},
    {"triangle-96000-heldout", 96000, OscillatorHandler::TRIANGLE},
  };
  for (const auto& test : cases) {
    case_directory = fs::path(argv[2]) / test.name;
    fs::create_directories(case_directory);
    PeriodicWaveImpl wave(test.rate);
    if (!wave.GenerateBasicWaveform(test.shape)) return 3;
  }
}
