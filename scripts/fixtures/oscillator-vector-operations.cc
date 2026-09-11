#include <array>
#include <cstdint>
#include <fstream>
#include <cmath>
#if defined(__aarch64__)
#include <arm_neon.h>
#else
#include <emmintrin.h>
#endif
struct Input { float frequency, lowest, cents, nyquist; };
int main(int argc, char** argv) {
  if (argc != 3) return 2;
#if defined(__aarch64__)
  uint64_t fp; asm volatile("mrs %0, fpcr" : "=r"(fp));
  fp |= uint64_t{1} << 24; asm volatile("msr fpcr, %0" :: "r"(fp));
#else
  _mm_setcsr(_mm_getcsr() | 0x8040);
#endif
  std::ifstream input(argv[1], std::ios::binary);
  std::ofstream output(argv[2], std::ios::binary);
  if (!input || !output) return 4;
  std::array<Input,4> row;
  while (input.read(reinterpret_cast<char*>(row.data()), sizeof(row))) {
    std::array<float,4> frequencies, cents, ratio_div, ratio_mul, pitch_div, pitch_mul;
    for (unsigned i=0;i<4;++i) {
      float f=row[i].frequency;
      f=std::isnan(f)?row[i].nyquist:std::fmin(std::fmax(f,-row[i].nyquist),row[i].nyquist);
      frequencies[i]=std::fabs(f); cents[i]=row[i].cents;
      if(row[i].lowest!=row[0].lowest)return 3;
    }
    const float low=row[0].lowest, inv_low=1.f/low, inv_cents=1.f/400.f;
#if defined(__aarch64__)
    auto f=vld1q_f32(frequencies.data()), c=vld1q_f32(cents.data());
    auto pos=vcgtq_f32(f,vdupq_n_f32(0.f));
    vst1q_f32(ratio_div.data(),vbslq_f32(pos,vdivq_f32(f,vdupq_n_f32(low)),vdupq_n_f32(.5f)));
    vst1q_f32(ratio_mul.data(),vbslq_f32(pos,vmulq_f32(f,vdupq_n_f32(inv_low)),vdupq_n_f32(.5f)));
    vst1q_f32(pitch_div.data(),vaddq_f32(vdupq_n_f32(1.f),vdivq_f32(c,vdupq_n_f32(400.f))));
    vst1q_f32(pitch_mul.data(),vaddq_f32(vdupq_n_f32(1.f),vmulq_f32(c,vdupq_n_f32(inv_cents))));
#else
    auto f=_mm_loadu_ps(frequencies.data()), c=_mm_loadu_ps(cents.data());
    auto pos=_mm_cmpgt_ps(f,_mm_setzero_ps());
    auto select=[&](auto r){return _mm_or_ps(_mm_and_ps(pos,r),_mm_andnot_ps(pos,_mm_set1_ps(.5f)));};
    _mm_storeu_ps(ratio_div.data(),select(_mm_div_ps(f,_mm_set1_ps(low))));
    _mm_storeu_ps(ratio_mul.data(),select(_mm_mul_ps(f,_mm_set1_ps(inv_low))));
    _mm_storeu_ps(pitch_div.data(),_mm_add_ps(_mm_set1_ps(1.f),_mm_div_ps(c,_mm_set1_ps(400.f))));
    _mm_storeu_ps(pitch_mul.data(),_mm_add_ps(_mm_set1_ps(1.f),_mm_mul_ps(c,_mm_set1_ps(inv_cents))));
#endif
    for(unsigned i=0;i<4;++i){const std::array<float,4> r={ratio_div[i],ratio_mul[i],pitch_div[i],pitch_mul[i]};output.write(reinterpret_cast<const char*>(r.data()),sizeof(r));}
  }
}
