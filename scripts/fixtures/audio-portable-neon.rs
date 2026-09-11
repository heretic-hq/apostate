// Diagnostic software semantics for the NEON intrinsics used by pinned RustFFT.
// Reuses the original NEON algorithm, lane schedule and butterfly expressions.
// Finite IEEE binary32/binary64 round-to-nearest, ties-to-even is assumed.
#![allow(non_camel_case_types, dead_code)]
pub type float32x2_t = [f32; 2];
pub type float32x4_t = [f32; 4];
pub type float64x2_t = [f64; 2];

macro_rules! binary {
    ($name:ident, $ty:ty, $n:expr, $op:tt) => {
        #[inline(always)] pub unsafe fn $name(a: [$ty; $n], b: [$ty; $n]) -> [$ty; $n] {
            std::array::from_fn(|i| a[i] $op b[i])
        }
    };
}
macro_rules! neg {
    ($name:ident, $ty:ty, $n:expr) => {
        #[inline(always)] pub unsafe fn $name(a: [$ty; $n]) -> [$ty; $n] {
            std::array::from_fn(|i| -a[i])
        }
    };
}
macro_rules! reinterpret {
    ($name:ident, $from:ty, $to:ty) => {
        #[inline(always)] pub unsafe fn $name(a: $from) -> $to { std::mem::transmute(a) }
    };
}
binary!(vadd_f32, f32, 2, +);
binary!(vsub_f32, f32, 2, -);
binary!(vaddq_f32, f32, 4, +);
binary!(vsubq_f32, f32, 4, -);
binary!(vmulq_f32, f32, 4, *);
binary!(vaddq_f64, f64, 2, +);
binary!(vsubq_f64, f64, 2, -);
binary!(vmulq_f64, f64, 2, *);
binary!(veor_u32, u32, 2, ^);
binary!(veorq_u32, u32, 4, ^);
binary!(veorq_u64, u64, 2, ^);
neg!(vneg_f32, f32, 2);
neg!(vneg_f64, f64, 1);
neg!(vnegq_f32, f32, 4);
neg!(vnegq_f64, f64, 2);

#[inline(always)] pub unsafe fn vfmaq_f32(acc: [f32;4], a: [f32;4], b: [f32;4]) -> [f32;4] {
    std::array::from_fn(|i| a[i].mul_add(b[i], acc[i]))
}
#[inline(always)] pub unsafe fn vfmaq_f64(acc: [f64;2], a: [f64;2], b: [f64;2]) -> [f64;2] {
    std::array::from_fn(|i| a[i].mul_add(b[i], acc[i]))
}
#[inline(always)] pub unsafe fn vfmaq_laneq_f64<const L: i32>(acc: [f64;2], a: [f64;2], b: [f64;2]) -> [f64;2] {
    assert!((0..2).contains(&L));
    std::array::from_fn(|i| a[i].mul_add(b[L as usize], acc[i]))
}
#[inline(always)] pub unsafe fn vmulq_laneq_f64<const L: i32>(a: [f64;2], b: [f64;2]) -> [f64;2] {
    assert!((0..2).contains(&L));
    std::array::from_fn(|i| a[i] * b[L as usize])
}
#[inline(always)] pub unsafe fn vmovq_n_f32(a: f32) -> [f32;4] { [a;4] }
#[inline(always)] pub unsafe fn vmovq_n_f64(a: f64) -> [f64;2] { [a;2] }

#[inline(always)] pub unsafe fn vget_low_f32(a: [f32;4]) -> [f32;2] { [a[0],a[1]] }
#[inline(always)] pub unsafe fn vget_high_f32(a: [f32;4]) -> [f32;2] { [a[2],a[3]] }
#[inline(always)] pub unsafe fn vget_low_f64(a: [f64;2]) -> [f64;1] { [a[0]] }
#[inline(always)] pub unsafe fn vget_high_f64(a: [f64;2]) -> [f64;1] { [a[1]] }
#[inline(always)] pub unsafe fn vcombine_f32(a: [f32;2], b: [f32;2]) -> [f32;4] { [a[0],a[1],b[0],b[1]] }
#[inline(always)] pub unsafe fn vcombine_f64(a: [f64;1], b: [f64;1]) -> [f64;2] { [a[0],b[0]] }
#[inline(always)] pub unsafe fn vtrn1q_f32(a: [f32;4], b: [f32;4]) -> [f32;4] { [a[0],b[0],a[2],b[2]] }
#[inline(always)] pub unsafe fn vtrn2q_f32(a: [f32;4], b: [f32;4]) -> [f32;4] { [a[1],b[1],a[3],b[3]] }
#[inline(always)] pub unsafe fn vtrn1q_f64(a: [f64;2], b: [f64;2]) -> [f64;2] { [a[0],b[0]] }
#[inline(always)] pub unsafe fn vtrn2q_f64(a: [f64;2], b: [f64;2]) -> [f64;2] { [a[1],b[1]] }
#[inline(always)] pub unsafe fn vrev64q_f32(a: [f32;4]) -> [f32;4] { [a[1],a[0],a[3],a[2]] }
#[inline(always)] pub unsafe fn vrev64_u32(a: [u32;2]) -> [u32;2] { [a[1],a[0]] }

reinterpret!(vreinterpret_f32_u32, [u32;2], [f32;2]);
reinterpret!(vreinterpret_u32_f32, [f32;2], [u32;2]);
reinterpret!(vreinterpretq_f32_f64, [f64;2], [f32;4]);
reinterpret!(vreinterpretq_f32_u32, [u32;4], [f32;4]);
reinterpret!(vreinterpretq_f32_u64, [u64;2], [f32;4]);
reinterpret!(vreinterpretq_f64_f32, [f32;4], [f64;2]);
reinterpret!(vreinterpretq_f64_u64, [u64;2], [f64;2]);
reinterpret!(vreinterpretq_u32_f32, [f32;4], [u32;4]);
reinterpret!(vreinterpretq_u64_f32, [f32;4], [u64;2]);
reinterpret!(vreinterpretq_u64_f64, [f64;2], [u64;2]);

#[inline(always)] pub unsafe fn vld1_f32(p: *const f32) -> [f32;2] {
    std::array::from_fn(|i| p.add(i).read_unaligned())
}
#[inline(always)] pub unsafe fn vld1q_f32(p: *const f32) -> [f32;4] {
    std::array::from_fn(|i| p.add(i).read_unaligned())
}
#[inline(always)] pub unsafe fn vld1q_f64(p: *const f64) -> [f64;2] {
    std::array::from_fn(|i| p.add(i).read_unaligned())
}
#[inline(always)] pub unsafe fn vld1q_dup_u64(p: *const u64) -> [u64;2] {
    [p.read_unaligned();2]
}
#[inline(always)] pub unsafe fn vld1q_lane_u64<const L: i32>(p: *const u64, mut a: [u64;2]) -> [u64;2] {
    assert!((0..2).contains(&L));
    a[L as usize] = p.read_unaligned();
    a
}
#[inline(always)] pub unsafe fn vst1_f32(p: *mut f32, a: [f32;2]) {
    for i in 0..2 { p.add(i).write_unaligned(a[i]); }
}
#[inline(always)] pub unsafe fn vst1q_f32(p: *mut f32, a: [f32;4]) {
    for i in 0..4 { p.add(i).write_unaligned(a[i]); }
}
#[inline(always)] pub unsafe fn vst1q_f64(p: *mut f64, a: [f64;2]) {
    for i in 0..2 { p.add(i).write_unaligned(a[i]); }
}
