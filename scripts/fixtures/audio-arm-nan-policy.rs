// Experimental operand-level policy observed in the pinned ARM controls.
// Operand priority remains subject to whole-kernel validation because LLVM
// can commute multiplication operands when lowering an intrinsic.
#[inline]
fn nan(value: u32) -> bool { value & 0x7fff_ffff > 0x7f80_0000 }
#[inline]
fn signaling(value: u32) -> bool { nan(value) && value & 0x0040_0000 == 0 }
#[inline]
fn quiet(value: u32) -> u32 { value | 0x0040_0000 }

pub fn binary(a: u32, b: u32) -> u32 {
    if signaling(a) { return quiet(a); }
    if signaling(b) { return quiet(b); }
    if nan(a) { return quiet(a); }
    if nan(b) { return quiet(b); }
    0x7fc0_0000
}

pub fn fused(acc: u32, a: u32, b: u32, flush_inputs: bool) -> u32 {
    // The measured ARM control lowers FMLA in accumulator, b, a order.
    for value in [acc, b, a] {
        if signaling(value) { return quiet(value); }
    }
    let zero = |value: u32| {
        let magnitude = value & 0x7fff_ffff;
        magnitude == 0 || (flush_inputs && magnitude < 0x0080_0000)
    };
    let infinity = |value: u32| value & 0x7fff_ffff == 0x7f80_0000;
    if (zero(a) && infinity(b)) || (zero(b) && infinity(a)) {
        return 0x7fc0_0000;
    }
    for value in [acc, b, a] {
        if nan(value) { return quiet(value); }
    }
    0x7fc0_0000
}

pub fn input_flushes() -> bool {
    #[cfg(target_arch = "x86_64")]
    unsafe {
        let mut control: u32 = 0;
        core::arch::asm!("stmxcsr [{}]", in(reg) &mut control, options(nostack, preserves_flags));
        return control & 0x40 != 0;
    }
    #[cfg(target_arch = "aarch64")]
    unsafe {
        let control: u64;
        core::arch::asm!("mrs {}, fpcr", out(reg) control, options(nomem, nostack, preserves_flags));
        return control & (1 << 24) != 0;
    }
    #[cfg(not(any(target_arch = "x86_64", target_arch = "aarch64")))]
    false
}
