mod wrapper;
use std::{env,fs,path::Path};
fn dump_complex(_: &str, _: &[rustfft::num_complex::Complex<f32>]) {}
fn read(path: &Path)->Vec<f32>{fs::read(path).unwrap().chunks_exact(4).map(|b|f32::from_le_bytes(b.try_into().unwrap())).collect()}
fn write(path: &Path,data:&[f32]){let bytes:Vec<u8>=data.iter().flat_map(|x|x.to_le_bytes()).collect();fs::write(path,bytes).unwrap();}
fn enable_audio_ftz(){
 #[cfg(target_arch="aarch64")]
 unsafe {let mut value:u64;core::arch::asm!("mrs {}, fpcr",out(reg)value);value|=1u64<<24;core::arch::asm!("msr fpcr, {}",in(reg)value);}
 #[cfg(target_arch="x86_64")]
 unsafe {let mut value:u32=0;core::arch::asm!("stmxcsr [{}]",in(reg)&mut value);value|=0x8040;core::arch::asm!("ldmxcsr [{}]",in(reg)&value);}
}
fn main(){
 let args:Vec<_>=env::args().collect();assert_eq!(args.len(),5);
 let phase=&args[1];let n:usize=args[2].parse().unwrap();let input=read(Path::new(&args[3]));let path=Path::new(&args[4]);
 if phase!="forward-kernel"{enable_audio_ftz();}
 let mut fft=wrapper::rustfft_new_for_profile(n,true);
 if phase.starts_with("forward"){
  assert_eq!(input.len(),n);let mut real=vec![0.;(n+1)/2];let mut imag=real.clone();fft.do_fft(&input,&mut real,&mut imag);real.extend_from_slice(&imag);write(path,&real);
 }else{assert_eq!(phase,"inverse");assert_eq!(input.len(),n);let mut output=vec![0.;n];fft.do_inverse_fft(&input[..n/2],&input[n/2..],&mut output);write(path,&output);}
}
