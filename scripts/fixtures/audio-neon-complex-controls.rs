// Fixed arbitrary inputs and lengths, declared before testing the adapter.
fn complex_controls(root: &std::path::Path) {
    use rustfft::{num_complex::Complex, FftDirection};
    for length in [16usize, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192] {
        for kind in ["impulse", "basis", "alternating", "sequence", "heldout", "zeros", "subnormal"] {
            let mut state = if kind == "heldout" { 0xc0ffeebau32 } else { 0x13579bdfu32 };
            let mut next = || {
                state ^= state << 13; state ^= state >> 17; state ^= state << 5;
                state
            };
            let input: Vec<Complex<f32>> = (0..length).map(|i| {
                match kind {
                    "impulse" => Complex::new(if i == 0 { 1.0 } else { 0.0 }, 0.0),
                    "basis" => Complex::new(if i == length / 3 { 0.5 } else { 0.0 },
                                            if i == length / 7 { -0.25 } else { 0.0 }),
                    "alternating" => Complex::new(if i % 2 == 0 { 0.25 } else { -0.25 },
                                                  if i % 3 == 0 { -0.125 } else { 0.125 }),
                    "zeros" => Complex::new(if i % 2 == 0 { 0.0 } else { -0.0 },
                                            if i % 3 == 0 { -0.0 } else { 0.0 }),
                    "subnormal" => Complex::new(f32::from_bits((next() & 4095) + 1),
                                                -f32::from_bits((next() & 4095) + 1)),
                    _ => Complex::new(((next() >> 16) as i32 - 32768) as f32 / 32768.0,
                                      ((next() >> 16) as i32 - 32768) as f32 / 32768.0),
                }
            }).collect();
            for direction in [FftDirection::Forward, FftDirection::Inverse] {
                #[cfg(feature = "portable-neon")]
                let mut planner = rustfft::FftPlannerNeon::<f32>::new().unwrap();
                #[cfg(not(feature = "portable-neon"))]
                let mut planner = rustfft::FftPlanner::<f32>::new();
                let fft = planner.plan_fft(length, direction);
                let mut output = input.clone();
                fft.process(&mut output);
                let dir = root.join(format!("complex-{length:05}-{kind}-{direction:?}"));
                std::fs::create_dir_all(&dir).unwrap();
                let flat_in: Vec<f32> = input.iter().flat_map(|x| [x.re, x.im]).collect();
                let flat_out: Vec<f32> = output.iter().flat_map(|x| [x.re, x.im]).collect();
                crate::write_floats(&dir.join("run.input.f32"), &flat_in);
                crate::write_floats(&dir.join("run.postfft.f32"), &flat_out);
            }
        }
    }
}
