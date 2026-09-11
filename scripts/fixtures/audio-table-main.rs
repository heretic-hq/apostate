// Diagnostic runner for the pinned Chromium RustFFT wrapper.
mod wrapper;
use std::{fs, path::{Path, PathBuf}, sync::Mutex};
pub static PREFIX: Mutex<Option<PathBuf>> = Mutex::new(None);

pub fn write_floats(path: &Path, samples: &[f32]) {
    let bytes: Vec<u8> = samples.iter().flat_map(|value| value.to_le_bytes()).collect();
    fs::write(path, bytes).unwrap();
}
pub fn dump_complex(stage: &str, samples: &[rustfft::num_complex::Complex<f32>]) {
    if let Some(prefix) = PREFIX.lock().unwrap().as_ref() {
        let data: Vec<f32> = samples.iter().flat_map(|value| [value.re, value.im]).collect();
        write_floats(&prefix.with_extension(format!("{stage}.f32")), &data);
    }
}
fn read_floats(path: &Path) -> Vec<f32> {
    let bytes = fs::read(path).unwrap();
    assert_eq!(bytes.len() % 4, 0);
    bytes.chunks_exact(4).map(|x| f32::from_le_bytes(x.try_into().unwrap())).collect()
}
fn main() {
    let root = PathBuf::from(std::env::args().nth(1).expect("stage directory"));
    let mut directories: Vec<_> = fs::read_dir(root).unwrap().map(|x| x.unwrap().path()).collect();
    directories.sort();
    for directory in directories {
        if !directory.is_dir() { continue; }
        let first = read_floats(&directory.join("range_0.real.f32"));
        let size = first.len() * 2;
        let mut frame = wrapper::rustfft_new(size);
        *PREFIX.lock().unwrap() = Some(directory.join("wrapper"));
        frame.diagnostic_dump_twiddles();
        let mut range = 0;
        loop {
            let prefix = directory.join(format!("range_{range}"));
            let real_path = prefix.with_extension("real.f32");
            if !real_path.exists() { break; }
            let real = read_floats(&real_path);
            let imag = read_floats(&prefix.with_extension("imag.f32"));
            let mut out = vec![0.0; size];
            *PREFIX.lock().unwrap() = Some(prefix.clone());
            frame.do_inverse_fft(&real, &imag, &mut out);
            write_floats(&prefix.with_extension("ifft.f32"), &out);
            range += 1;
        }
        eprintln!("diagnostic-case:{} size:{size} ranges:{range}", directory.display());
    }
}
