// Copyright 2026 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.
#ifndef THIRD_PARTY_BLINK_RENDERER_PLATFORM_AUDIO_MAC_ARM_FFT_MULTIPLY_H_
#define THIRD_PARTY_BLINK_RENDERER_PLATFORM_AUDIO_MAC_ARM_FFT_MULTIPLY_H_
#include <bit>
#include <cmath>
#include <cstdint>
#include "base/compiler_specific.h"
#include "base/containers/span.h"
#include "build/build_config.h"
#if defined(ARCH_CPU_X86_FAMILY)
#include <immintrin.h>
#endif
namespace blink::mac_arm_fft_multiply {
// Mac ARM complex multiplication rounds the first product, then fuses the
// second product with it. NaN propagation belongs to these primitive operations:
// signaling operands win, followed by accumulator-first quiet NaNs for FMA.
// Invalid arithmetic without a selected operand produces a positive quiet NaN.
// No output buffer is canonicalized, and finite arithmetic remains f32.
inline bool IsNaN(uint32_t value) {
  return (value & 0x7fffffffu) > 0x7f800000u;
}
inline bool IsSignaling(uint32_t value) {
  return IsNaN(value) && !(value & 0x00400000u);
}
inline bool FlushInputs() {
#if defined(ARCH_CPU_ARM64)
  uint64_t control;
  asm volatile("mrs %0, fpcr" : "=r"(control));
  return control & (uint64_t{1} << 24);
#elif defined(ARCH_CPU_X86_FAMILY)
  return _mm_getcsr() & 0x40;
#else
  return false;
#endif
}
ALWAYS_INLINE float MultiplyScalar(float a, float b) {
  const float result = a * b;
  if (!std::isnan(result)) return result;
  const uint32_t x = std::bit_cast<uint32_t>(a);
  const uint32_t y = std::bit_cast<uint32_t>(b);
  uint32_t selected = 0x7fc00000u;
  if (IsSignaling(x)) selected = x;
  else if (IsSignaling(y)) selected = y;
  else if (IsNaN(x)) selected = x;
  else if (IsNaN(y)) selected = y;
  return std::bit_cast<float>(selected | 0x00400000u);
}
ALWAYS_INLINE float FmaScalar(float a, float b, float accumulator) {
  const float result = std::fma(a, b, accumulator);
  if (!std::isnan(result)) return result;
  const uint32_t x = std::bit_cast<uint32_t>(a);
  const uint32_t y = std::bit_cast<uint32_t>(b);
  const uint32_t z = std::bit_cast<uint32_t>(accumulator);
  uint32_t selected = 0x7fc00000u;
  if (IsSignaling(z)) selected = z;
  else if (IsSignaling(x)) selected = x;
  else if (IsSignaling(y)) selected = y;
  else {
    const bool flush = FlushInputs();
    auto zero = [flush](uint32_t bits) {
      bits &= 0x7fffffffu;
      return bits == 0 || (flush && bits < 0x00800000u);
    };
    auto infinity = [](uint32_t bits) {
      return (bits & 0x7fffffffu) == 0x7f800000u;
    };
    if (!((zero(x) && infinity(y)) || (zero(y) && infinity(x)))) {
      if (IsNaN(z)) selected = z;
      else if (IsNaN(x)) selected = x;
      else if (IsNaN(y)) selected = y;
    }
  }
  return std::bit_cast<float>(selected | 0x00400000u);
}
inline void Portable(base::span<const float> ar, base::span<const float> ai,
                     base::span<const float> br, base::span<const float> bi,
                     base::span<float> real, base::span<float> imag) {
  for (size_t i = 0; i < ar.size(); ++i) {
    const float r1 = ar[i], i1 = ai[i], r2 = br[i], i2 = bi[i];
    const float negative_i1 = std::bit_cast<float>(
        std::bit_cast<uint32_t>(i1) ^ 0x80000000u);
    real[i] = FmaScalar(negative_i1, i2, MultiplyScalar(r1, r2));
    imag[i] = FmaScalar(i1, r2, MultiplyScalar(r1, i2));
  }
}
#if defined(ARCH_CPU_X86_64)
namespace internal {
ALWAYS_INLINE __m128i Select(__m128i mask, __m128i yes, __m128i no) {
  return _mm_or_si128(_mm_and_si128(mask, yes), _mm_andnot_si128(mask, no));
}
ALWAYS_INLINE __m128i Magnitude(__m128i bits) {
  return _mm_and_si128(bits, _mm_set1_epi32(0x7fffffff));
}
ALWAYS_INLINE __m128i NaNMask(__m128i bits) {
  return _mm_cmpgt_epi32(Magnitude(bits), _mm_set1_epi32(0x7f800000));
}
ALWAYS_INLINE __m128i SignalingMask(__m128i bits) {
  return _mm_and_si128(NaNMask(bits), _mm_cmpeq_epi32(
      _mm_and_si128(bits, _mm_set1_epi32(0x00400000)), _mm_setzero_si128()));
}
ALWAYS_INLINE bool AnyNaN(__m128 a, __m128 b) {
  return _mm_movemask_ps(_mm_or_ps(_mm_cmpunord_ps(a, a),
                                   _mm_cmpunord_ps(b, b))) != 0;
}
ALWAYS_INLINE __m128 FixMultiply(__m128 a, __m128 b, __m128 result) {
  const auto x = _mm_castps_si128(a), y = _mm_castps_si128(b);
  const auto nx = NaNMask(x), ny = NaNMask(y);
  const auto sx = SignalingMask(x), sy = SignalingMask(y);
  const auto quiet = Select(_mm_or_si128(nx, ny), Select(nx, x, y),
                            _mm_set1_epi32(0x7fc00000));
  auto selected = Select(_mm_or_si128(sx, sy), Select(sx, x, y), quiet);
  selected = _mm_or_si128(selected, _mm_set1_epi32(0x00400000));
  return _mm_castsi128_ps(Select(NaNMask(_mm_castps_si128(result)), selected,
                                _mm_castps_si128(result)));
}
ALWAYS_INLINE __m128i ZeroMask(__m128i bits, bool flush) {
  return flush ? _mm_cmpgt_epi32(_mm_set1_epi32(0x00800000), Magnitude(bits))
               : _mm_cmpeq_epi32(Magnitude(bits), _mm_setzero_si128());
}
ALWAYS_INLINE __m128 FixFma(__m128 a, __m128 b, __m128 accumulator,
                            __m128 result, bool flush) {
  const auto x = _mm_castps_si128(a), y = _mm_castps_si128(b);
  const auto z = _mm_castps_si128(accumulator);
  const auto nx = NaNMask(x), ny = NaNMask(y), nz = NaNMask(z);
  const auto sx = SignalingMask(x), sy = SignalingMask(y), sz = SignalingMask(z);
  const auto any_nan = _mm_or_si128(nz, _mm_or_si128(nx, ny));
  const auto any_signaling = _mm_or_si128(sz, _mm_or_si128(sx, sy));
  const auto first_nan = Select(nz, z, Select(nx, x, y));
  const auto first_signaling = Select(sz, z, Select(sx, x, y));
  const auto infinity = _mm_set1_epi32(0x7f800000);
  const auto invalid_product = _mm_or_si128(
      _mm_and_si128(ZeroMask(x, flush), _mm_cmpeq_epi32(Magnitude(y), infinity)),
      _mm_and_si128(ZeroMask(y, flush), _mm_cmpeq_epi32(Magnitude(x), infinity)));
  const auto canonical = _mm_set1_epi32(0x7fc00000);
  auto quiet = Select(any_nan, first_nan, canonical);
  quiet = Select(invalid_product, canonical, quiet);
  auto selected = Select(any_signaling, first_signaling, quiet);
  selected = _mm_or_si128(selected, _mm_set1_epi32(0x00400000));
  return _mm_castsi128_ps(Select(NaNMask(_mm_castps_si128(result)), selected,
                                _mm_castps_si128(result)));
}
}  // namespace internal

__attribute__((target("fma"))) inline void Fma(
    base::span<const float> ar, base::span<const float> ai,
    base::span<const float> br, base::span<const float> bi,
    base::span<float> real, base::span<float> imag) {
  const bool flush = FlushInputs();
  while (ar.size() >= 4) {
    const auto r1 = _mm_loadu_ps(ar.first(4u).data());
    const auto i1 = _mm_loadu_ps(ai.first(4u).data());
    const auto r2 = _mm_loadu_ps(br.first(4u).data());
    const auto i2 = _mm_loadu_ps(bi.first(4u).data());
    auto base_real = _mm_mul_ps(r1, r2);
    auto base_imag = _mm_mul_ps(r1, i2);
    if (internal::AnyNaN(base_real, base_imag)) [[unlikely]] {
      base_real = internal::FixMultiply(r1, r2, base_real);
      base_imag = internal::FixMultiply(r1, i2, base_imag);
    }
    const auto negative_i1 = _mm_xor_ps(i1, _mm_set1_ps(-0.f));
    auto result_real = _mm_fmadd_ps(negative_i1, i2, base_real);
    auto result_imag = _mm_fmadd_ps(i1, r2, base_imag);
    if (internal::AnyNaN(result_real, result_imag)) [[unlikely]] {
      result_real = internal::FixFma(negative_i1, i2, base_real,
                                     result_real, flush);
      result_imag = internal::FixFma(i1, r2, base_imag, result_imag, flush);
    }
    _mm_storeu_ps(real.first(4u).data(), result_real);
    _mm_storeu_ps(imag.first(4u).data(), result_imag);
    ar = ar.subspan(4u); ai = ai.subspan(4u);
    br = br.subspan(4u); bi = bi.subspan(4u);
    real = real.subspan(4u); imag = imag.subspan(4u);
  }
  Portable(ar, ai, br, bi, real, imag);
}
#endif
#if defined(ARCH_CPU_X86_64)
namespace internal {
__attribute__((target("fma"))) ALWAYS_INLINE __m256 OrderedMul256(__m256 a, __m256 b) {
  __m256 result;
  asm volatile("vmulps %2, %1, %0" : "=x"(result) : "x"(a), "x"(b));
  return result;
}
__attribute__((target("fma"))) ALWAYS_INLINE __m256 OrderedFma256(__m256 accumulator, __m256 a, __m256 b) {
  asm volatile("vfmadd231ps %2, %1, %0" : "+x"(accumulator) : "x"(a), "x"(b));
  return accumulator;
}
__attribute__((target("fma"))) ALWAYS_INLINE __m256 AccumulatorFirst256(__m256 accumulator, __m256 result) {
  return _mm256_blendv_ps(result, accumulator, _mm256_cmp_ps(accumulator, accumulator, _CMP_UNORD_Q));
}
__attribute__((target("fma"))) ALWAYS_INLINE __m256 SignalingOrInfinity256(__m256 value) {
  const auto masked = _mm256_and_ps(value, _mm256_set1_ps(std::bit_cast<float>(0x7fc00000u)));
  return _mm256_cmp_ps(masked, _mm256_set1_ps(std::bit_cast<float>(0x7f800000u)), _CMP_EQ_OQ);
}
}
__attribute__((target("fma"))) inline void Fast(
    base::span<const float> ar, base::span<const float> ai,
    base::span<const float> br, base::span<const float> bi,
    base::span<float> real, base::span<float> imag) {
  while (ar.size() >= 8) {
    const auto r1 = _mm256_loadu_ps(ar.first(8u).data());
    const auto i1 = _mm256_loadu_ps(ai.first(8u).data());
    const auto r2 = _mm256_loadu_ps(br.first(8u).data());
    const auto i2 = _mm256_loadu_ps(bi.first(8u).data());
    auto special = _mm256_or_ps(
        _mm256_or_ps(internal::SignalingOrInfinity256(r1), internal::SignalingOrInfinity256(i1)),
        _mm256_or_ps(internal::SignalingOrInfinity256(r2), internal::SignalingOrInfinity256(i2)));
    // With finite or quiet-NaN multiplicands, only quiet-NaN selection needs
    // adjustment. An overflowing rounded first product is infinity, while the
    // fused finite product is not rounded separately and cannot be 0 * infinity.
    // Signaling NaNs and infinities use the complete primitive policy below.
    if (_mm256_movemask_ps(special)) [[unlikely]] {
      Fma(ar, ai, br, bi, real, imag);
      return;
    } else {
      const auto base_real = internal::OrderedMul256(r1, r2);
      const auto base_imag = internal::OrderedMul256(r1, i2);
      const auto negative_i1 = _mm256_xor_ps(i1, _mm256_set1_ps(-0.f));
      _mm256_storeu_ps(real.first(8u).data(), internal::AccumulatorFirst256(base_real, internal::OrderedFma256(base_real, negative_i1, i2)));
      _mm256_storeu_ps(imag.first(8u).data(), internal::AccumulatorFirst256(base_imag, internal::OrderedFma256(base_imag, i1, r2)));
    }
    ar = ar.subspan(8u); ai = ai.subspan(8u);
    br = br.subspan(8u); bi = bi.subspan(8u);
    real = real.subspan(8u); imag = imag.subspan(8u);
  }
  Portable(ar, ai, br, bi, real, imag);
}
#endif
using Function = void (*)(base::span<const float>, base::span<const float>,
                          base::span<const float>, base::span<const float>,
                          base::span<float>, base::span<float>);
}  // namespace blink::mac_arm_fft_multiply
#endif  // THIRD_PARTY_BLINK_RENDERER_PLATFORM_AUDIO_MAC_ARM_FFT_MULTIPLY_H_
