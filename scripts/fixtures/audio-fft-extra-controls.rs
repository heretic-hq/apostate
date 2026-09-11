// Standalone controls for the candidate factory and browser audio-thread FP mode.
#[inline(never)]
fn fp_control() -> u64 {
    #[cfg(target_arch = "aarch64")]
    unsafe {
        let value: u64;
        core::arch::asm!("mrs {0}, fpcr", out(reg) value, options(nomem, nostack, preserves_flags));
        value
    }
    #[cfg(target_arch = "x86_64")]
    unsafe {
        let mut value: u32 = 0;
        core::arch::asm!("stmxcsr [{0}]", in(reg) &mut value, options(nostack, preserves_flags));
        value as u64
    }
}
#[inline(never)]
fn set_fp_control(value: u64) {
    #[cfg(target_arch = "aarch64")]
    unsafe { core::arch::asm!("msr fpcr, {0}", in(reg) value, options(nostack, preserves_flags)); }
    #[cfg(target_arch = "x86_64")]
    unsafe {
        let value = value as u32;
        core::arch::asm!("ldmxcsr [{0}]", in(reg) &value, options(nostack, preserves_flags));
    }
}
fn fp_flush_mask() -> u64 {
    #[cfg(target_arch = "aarch64")] { 1 << 24 }
    #[cfg(target_arch = "x86_64")] { 0x8040 }
}

fn extra_controls(root: &std::path::Path) {
    let saved = fp_control();
    set_fp_control(saved & !fp_flush_mask());
    let enabled = fp_control();
    complex_controls(root, "default");
    set_fp_control(saved | fp_flush_mask());
    let flushed = fp_control();
    complex_controls(root, "ftz");
    set_fp_control(saved);
    wrapper::diagnostic_cache_check();
    std::fs::write(root.parent().unwrap().join("fp-environment.json"), format!(
        "{{\"saved\":{saved},\"denormals_enabled\":{enabled},\"flush_to_zero\":{flushed},\"cache_mode_check\":true}}\n"
    )).unwrap();
    benchmark_kernels(root.parent().unwrap());
}

fn benchmark_kernels(root: &std::path::Path) {
    use rustfft::{Fft, FftDirection, num_complex::Complex};
    use std::{sync::Arc, time::Instant, hint::black_box};
    let mut records = Vec::new();
    for portable in [false, true] {
        for size in [256usize, 1024, 2048, 8192, 32768] {
            let kernel: Arc<dyn Fft<f32>> = wrapper::diagnostic_plan(size, FftDirection::Inverse, portable);
            let input: Vec<Complex<f32>> = (0..size).map(|i| Complex::new(
                ((i * 17 % 1024) as i32 - 512) as f32 / 1024.0,
                ((i * 29 % 2048) as i32 - 1024) as f32 / 2048.0)).collect();
            let mut output = input.clone();
            let mut scratch = vec![Complex::new(0.0, 0.0); kernel.get_inplace_scratch_len()];
            for _ in 0..16 {
                output.copy_from_slice(&input);
                kernel.process_with_scratch(&mut output, &mut scratch);
            }
            let iterations = (8_388_608 / size).max(64);
            let mut timings = Vec::new();
            for _ in 0..3 {
                let start = Instant::now();
                for _ in 0..iterations {
                    output.copy_from_slice(black_box(&input));
                    kernel.process_with_scratch(black_box(&mut output), &mut scratch);
                    black_box(&output);
                }
                timings.push(start.elapsed().as_nanos() as f64 / iterations as f64);
            }
            timings.sort_by(|a,b| a.partial_cmp(b).unwrap());
            records.push(format!("{{\"portable\":{portable},\"complex_size\":{size},\"iterations\":{iterations},\"median_ns_with_input_reset\":{},\"samples_ns\":[{},{},{}]}}",
                                 timings[1], timings[0], timings[1], timings[2]));
        }
    }
    std::fs::write(root.join("kernel-benchmark.json"), format!("[{}]\n", records.join(","))).unwrap();
}
