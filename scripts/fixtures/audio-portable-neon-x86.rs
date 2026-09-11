// NEON lane semantics implemented with x86_64's baseline SSE2 operations.
// FMA is selected at runtime; the fallback preserves single-round mul_add.
#![allow(non_camel_case_types, dead_code)]
use core::arch::x86_64::*;
pub type float32x2_t = [f32;2];
pub type float32x4_t = __m128;
pub type float64x2_t = __m128d;

#[inline(always)] fn scalar_nan(value:f32,a:f32,b:f32)->f32 {
    if value.is_nan() {f32::from_bits(super::nan::binary(a.to_bits(),b.to_bits()))}else{value}
}
#[inline(always)] pub unsafe fn vadd_f32(a:[f32;2],b:[f32;2])->[f32;2] {[scalar_nan(a[0]+b[0],a[0],b[0]),scalar_nan(a[1]+b[1],a[1],b[1])]}
#[inline(always)] pub unsafe fn vsub_f32(a:[f32;2],b:[f32;2])->[f32;2] {[scalar_nan(a[0]-b[0],a[0],b[0]),scalar_nan(a[1]-b[1],a[1],b[1])]}
#[inline(always)] pub unsafe fn vneg_f32(a:[f32;2])->[f32;2] {[-a[0],-a[1]]}
#[inline(always)] pub unsafe fn vneg_f64(a:[f64;1])->[f64;1] {[-a[0]]}
#[cold]
unsafe fn binary_nan_values(out:__m128,a:__m128,b:__m128)->__m128 {
    let mut result:[f32;4]=std::mem::transmute(out);
    let aa:[f32;4]=std::mem::transmute(a);let bb:[f32;4]=std::mem::transmute(b);
    for i in 0..4 {if result[i].is_nan() {result[i]=f32::from_bits(super::nan::binary(aa[i].to_bits(),bb[i].to_bits()));}}
    std::mem::transmute(result)
}
#[inline(always)] unsafe fn binary_result(out:__m128,a:__m128,b:__m128)->__m128 {
    if _mm_movemask_ps(_mm_cmpunord_ps(out,out))!=0 {binary_nan_values(out,a,b)}else{out}
}
#[inline(always)] pub unsafe fn vaddq_f32(a:__m128,b:__m128)->__m128 {binary_result(_mm_add_ps(a,b),a,b)}
#[inline(always)] pub unsafe fn vsubq_f32(a:__m128,b:__m128)->__m128 {binary_result(_mm_sub_ps(a,b),a,b)}
#[inline(always)] pub unsafe fn vmulq_f32(a:__m128,b:__m128)->__m128 {binary_result(_mm_mul_ps(a,b),a,b)}
#[inline(always)] pub unsafe fn vaddq_f64(a:__m128d,b:__m128d)->__m128d {_mm_add_pd(a,b)}
#[inline(always)] pub unsafe fn vsubq_f64(a:__m128d,b:__m128d)->__m128d {_mm_sub_pd(a,b)}
#[inline(always)] pub unsafe fn vmulq_f64(a:__m128d,b:__m128d)->__m128d {_mm_mul_pd(a,b)}
#[inline(always)] pub unsafe fn vnegq_f32(a:__m128)->__m128 {_mm_xor_ps(a,_mm_set1_ps(-0.0))}
#[inline(always)] pub unsafe fn vnegq_f64(a:__m128d)->__m128d {_mm_xor_pd(a,_mm_set1_pd(-0.0))}
#[inline(always)] pub unsafe fn veor_u32(a:[u32;2],b:[u32;2])->[u32;2] {[a[0]^b[0],a[1]^b[1]]}
#[inline(always)] pub unsafe fn veorq_u32(a:__m128i,b:__m128i)->__m128i {_mm_xor_si128(a,b)}
#[inline(always)] pub unsafe fn veorq_u64(a:__m128i,b:__m128i)->__m128i {_mm_xor_si128(a,b)}

#[target_feature(enable="fma")]
unsafe fn fused32(acc:__m128,a:__m128,b:__m128)->__m128 {_mm_fmadd_ps(a,b,acc)}
#[target_feature(enable="fma")]
unsafe fn fused64(acc:__m128d,a:__m128d,b:__m128d)->__m128d {_mm_fmadd_pd(a,b,acc)}
#[cold]
unsafe fn fused_nan_values(out:__m128,acc:__m128,a:__m128,b:__m128)->__m128 {
    let mut result:[f32;4]=std::mem::transmute(out);
    let aa:[f32;4]=std::mem::transmute(a);let bb:[f32;4]=std::mem::transmute(b);
    let cc:[f32;4]=std::mem::transmute(acc);let flush=super::nan::input_flushes();
    for i in 0..4 {if result[i].is_nan() {result[i]=f32::from_bits(super::nan::fused(cc[i].to_bits(),aa[i].to_bits(),bb[i].to_bits(),flush));}}
    std::mem::transmute(result)
}
#[inline(always)] pub unsafe fn vfmaq_f32(acc:__m128,a:__m128,b:__m128)->__m128 {
    let result=if super::HARDWARE_FMA { _mm_fmadd_ps(a,b,acc) }
    else if std::arch::is_x86_feature_detected!("fma") { fused32(acc,a,b) }
    else {
        let aa:[f32;4]=std::mem::transmute(a);let bb:[f32;4]=std::mem::transmute(b);
        let cc:[f32;4]=std::mem::transmute(acc);
        std::mem::transmute(std::array::from_fn::<f32,4,_>(|i| aa[i].mul_add(bb[i],cc[i])))
    };
    if _mm_movemask_ps(_mm_cmpunord_ps(result,result))!=0 {fused_nan_values(result,acc,a,b)}else{result}
}
#[inline(always)] pub unsafe fn vfmaq_f64(acc:__m128d,a:__m128d,b:__m128d)->__m128d {
    if super::HARDWARE_FMA { return _mm_fmadd_pd(a,b,acc); }
    if std::arch::is_x86_feature_detected!("fma") { return fused64(acc,a,b); }
    let aa:[f64;2]=std::mem::transmute(a);
    let bb:[f64;2]=std::mem::transmute(b);
    let cc:[f64;2]=std::mem::transmute(acc);
    std::mem::transmute([aa[0].mul_add(bb[0],cc[0]),aa[1].mul_add(bb[1],cc[1])])
}
#[inline(always)] pub unsafe fn vmulq_laneq_f64<const L:i32>(a:__m128d,b:__m128d)->__m128d {
    assert!(L==0 || L==1);
    _mm_mul_pd(a,if L==0 {_mm_unpacklo_pd(b,b)}else{_mm_unpackhi_pd(b,b)})
}
#[inline(always)] pub unsafe fn vfmaq_laneq_f64<const L:i32>(acc:__m128d,a:__m128d,b:__m128d)->__m128d {
    assert!(L==0 || L==1);
    vfmaq_f64(acc,a,if L==0 {_mm_unpacklo_pd(b,b)}else{_mm_unpackhi_pd(b,b)})
}
#[inline(always)] pub unsafe fn vmovq_n_f32(a:f32)->__m128 {_mm_set1_ps(a)}
#[inline(always)] pub unsafe fn vmovq_n_f64(a:f64)->__m128d {_mm_set1_pd(a)}
#[inline(always)] pub unsafe fn vget_low_f32(a:__m128)->[f32;2] {
    let mut out=[0.0;2];_mm_storel_epi64(out.as_mut_ptr().cast(),_mm_castps_si128(a));out
}
#[inline(always)] pub unsafe fn vget_high_f32(a:__m128)->[f32;2] {vget_low_f32(_mm_movehl_ps(a,a))}
#[inline(always)] pub unsafe fn vget_low_f64(a:__m128d)->[f64;1] {[_mm_cvtsd_f64(a)]}
#[inline(always)] pub unsafe fn vget_high_f64(a:__m128d)->[f64;1] {[_mm_cvtsd_f64(_mm_unpackhi_pd(a,a))]}
#[inline(always)] pub unsafe fn vcombine_f32(a:[f32;2],b:[f32;2])->__m128 {
    _mm_castsi128_ps(_mm_unpacklo_epi64(_mm_loadl_epi64(a.as_ptr().cast()),_mm_loadl_epi64(b.as_ptr().cast())))
}
#[inline(always)] pub unsafe fn vcombine_f64(a:[f64;1],b:[f64;1])->__m128d {_mm_set_pd(b[0],a[0])}
#[inline(always)] pub unsafe fn vtrn1q_f32(a:__m128,b:__m128)->__m128 {
    let v=_mm_shuffle_ps::<0x88>(a,b);_mm_shuffle_ps::<0xd8>(v,v)
}
#[inline(always)] pub unsafe fn vtrn2q_f32(a:__m128,b:__m128)->__m128 {
    let v=_mm_shuffle_ps::<0xdd>(a,b);_mm_shuffle_ps::<0xd8>(v,v)
}
#[inline(always)] pub unsafe fn vtrn1q_f64(a:__m128d,b:__m128d)->__m128d {_mm_unpacklo_pd(a,b)}
#[inline(always)] pub unsafe fn vtrn2q_f64(a:__m128d,b:__m128d)->__m128d {_mm_unpackhi_pd(a,b)}
#[inline(always)] pub unsafe fn vrev64q_f32(a:__m128)->__m128 {_mm_shuffle_ps::<0xb1>(a,a)}
#[inline(always)] pub unsafe fn vrev64_u32(a:[u32;2])->[u32;2] {[a[1],a[0]]}
#[inline(always)] pub unsafe fn vreinterpret_f32_u32(a:[u32;2])->[f32;2] {std::mem::transmute(a)}
#[inline(always)] pub unsafe fn vreinterpret_u32_f32(a:[f32;2])->[u32;2] {std::mem::transmute(a)}
#[inline(always)] pub unsafe fn vreinterpretq_f32_f64(a:__m128d)->__m128 {_mm_castpd_ps(a)}
#[inline(always)] pub unsafe fn vreinterpretq_f32_u32(a:__m128i)->__m128 {_mm_castsi128_ps(a)}
#[inline(always)] pub unsafe fn vreinterpretq_f32_u64(a:__m128i)->__m128 {_mm_castsi128_ps(a)}
#[inline(always)] pub unsafe fn vreinterpretq_f64_f32(a:__m128)->__m128d {_mm_castps_pd(a)}
#[inline(always)] pub unsafe fn vreinterpretq_f64_u64(a:__m128i)->__m128d {_mm_castsi128_pd(a)}
#[inline(always)] pub unsafe fn vreinterpretq_u32_f32(a:__m128)->__m128i {_mm_castps_si128(a)}
#[inline(always)] pub unsafe fn vreinterpretq_u64_f32(a:__m128)->__m128i {_mm_castps_si128(a)}
#[inline(always)] pub unsafe fn vreinterpretq_u64_f64(a:__m128d)->__m128i {_mm_castpd_si128(a)}
#[inline(always)] pub unsafe fn vld1_f32(p:*const f32)->[f32;2] {[p.read_unaligned(),p.add(1).read_unaligned()]}
#[inline(always)] pub unsafe fn vld1q_f32(p:*const f32)->__m128 {_mm_loadu_ps(p)}
#[inline(always)] pub unsafe fn vld1q_f64(p:*const f64)->__m128d {_mm_loadu_pd(p)}
#[inline(always)] pub unsafe fn vld1q_dup_u64(p:*const u64)->__m128i {
    let v=_mm_loadl_epi64(p.cast());_mm_unpacklo_epi64(v,v)
}
#[inline(always)] pub unsafe fn vld1q_lane_u64<const L:i32>(p:*const u64,a:__m128i)->__m128i {
    assert!(L==0 || L==1);let v=_mm_loadl_epi64(p.cast());
    if L==0 {_mm_unpacklo_epi64(v,_mm_srli_si128::<8>(a))}else{_mm_unpacklo_epi64(a,v)}
}
#[inline(always)] pub unsafe fn vst1_f32(p:*mut f32,a:[f32;2]) {p.write_unaligned(a[0]);p.add(1).write_unaligned(a[1]);}
#[inline(always)] pub unsafe fn vst1q_f32(p:*mut f32,a:__m128) {_mm_storeu_ps(p,a)}
#[inline(always)] pub unsafe fn vst1q_f64(p:*mut f64,a:__m128d) {_mm_storeu_pd(p,a)}
