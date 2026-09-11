// Ordered native arithmetic controls. Inputs and outputs remain raw float32 bits.
use std::{fs::OpenOptions, io::{BufWriter, Write}, hint::black_box};

#[cfg(target_arch="aarch64")]
#[inline(never)]
#[target_feature(enable="neon")]
unsafe fn apply(op:u8,a:*const u32,b:*const u32,c:*const u32)->[u32;4] {
    use core::arch::aarch64::*;
    let aa=vreinterpretq_f32_u32(vld1q_u32(a));
    let bb=vreinterpretq_f32_u32(vld1q_u32(b));
    let cc=vreinterpretq_f32_u32(vld1q_u32(c));
    let out=match op {
        0=>vaddq_f32(aa,bb), 1=>vsubq_f32(aa,bb), 2=>vmulq_f32(aa,bb),
        3=>vfmaq_f32(cc,aa,bb), 4=>vnegq_f32(aa), _=>unreachable!(),
    };
    let mut result=[0u32;4];vst1q_u32(result.as_mut_ptr(),vreinterpretq_u32_f32(out));result
}
#[cfg(target_arch="x86_64")]
#[inline(never)]
#[target_feature(enable="fma")]
unsafe fn apply(op:u8,a:*const u32,b:*const u32,c:*const u32)->[u32;4] {
    use core::arch::x86_64::*;
    let aa=_mm_castsi128_ps(_mm_loadu_si128(a.cast()));
    let bb=_mm_castsi128_ps(_mm_loadu_si128(b.cast()));
    let cc=_mm_castsi128_ps(_mm_loadu_si128(c.cast()));
    let out=match op {
        0=>_mm_add_ps(aa,bb), 1=>_mm_sub_ps(aa,bb), 2=>_mm_mul_ps(aa,bb),
        3=>_mm_fmadd_ps(aa,bb,cc), 4=>_mm_xor_ps(aa,_mm_set1_ps(-0.0)), _=>unreachable!(),
    };
    let mut result=[0u32;4];_mm_storeu_si128(result.as_mut_ptr().cast(),_mm_castps_si128(out));result
}
fn get_control()->u64 {
    #[cfg(target_arch="aarch64")]
    unsafe {let x:u64;core::arch::asm!("mrs {}, fpcr",out(reg)x,options(nomem,nostack,preserves_flags));x}
    #[cfg(target_arch="x86_64")]
    unsafe {let mut x:u32=0;core::arch::asm!("stmxcsr [{}]",in(reg)&mut x,options(nostack,preserves_flags));x as u64}
}
fn set_control(x:u64) {
    #[cfg(target_arch="aarch64")]
    unsafe {core::arch::asm!("msr fpcr, {}",in(reg)x,options(nostack,preserves_flags));}
    #[cfg(target_arch="x86_64")]
    unsafe {let x=x as u32;core::arch::asm!("ldmxcsr [{}]",in(reg)&x,options(nostack,preserves_flags));}
}
fn main() {
    #[cfg(target_arch="x86_64")]
    assert!(std::arch::is_x86_feature_detected!("fma"), "native primitive control requires FMA support");
    let output=std::env::args().nth(1).expect("new output CSV path");
    let file=OpenOptions::new().write(true).create_new(true).open(output).unwrap();
    let mut out=BufWriter::new(file);
    let saved=get_control();
    #[cfg(target_arch="aarch64")] let mask=1u64<<24;
    #[cfg(target_arch="x86_64")] let mask=0x8040u64;
    writeln!(out,"# arch={} saved_control={saved:#x} flush_mask={mask:#x}",std::env::consts::ARCH).unwrap();
    writeln!(out,"mode,operation,lane,a,b,c,result").unwrap();
    let values=[0x00000000u32,0x80000000,0x3f800000,0xbf800000,0x7f800000,0xff800000,
                0x7fc12345,0xffc23456,0x7f812345,0xff823456,0x7f7fffff,0xff7fffff,
                0x00000001,0x80000001];
    for flush in [false,true] {
        set_control(if flush {saved|mask}else{saved & !mask});
        let mode=if flush {"ftz"}else{"default"};
        for op in 0..5u8 {
            for (ia,_) in values.iter().enumerate() {
                let bs:&[u32]=if op==4 {&[0]}else{&values};
                let cs:&[u32]=if op==3 {&values}else{&[0]};
                for (ib,_) in bs.iter().enumerate() {for (ic,_) in cs.iter().enumerate() {
                    let aa:[u32;4]=std::array::from_fn(|lane| values[(ia+lane)%values.len()]);
                    let bb:[u32;4]=std::array::from_fn(|lane| if op==4 {0}else{values[(ib+3*lane)%values.len()]});
                    let cc:[u32;4]=std::array::from_fn(|lane| if op!=3 {0}else{values[(ic+5*lane)%values.len()]});
                    let result=unsafe{apply(black_box(op),black_box(aa.as_ptr()),black_box(bb.as_ptr()),black_box(cc.as_ptr()))};
                    for lane in 0..4 {
                        writeln!(out,"{mode},{},{lane},{:08x},{:08x},{:08x},{:08x}",
                                 ["add","sub","mul","fma","neg"][op as usize],aa[lane],bb[lane],cc[lane],result[lane]).unwrap();
                    }
                }}
            }
        }
    }
    set_control(saved);
}
