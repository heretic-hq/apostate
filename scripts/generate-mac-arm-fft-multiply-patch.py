#!/usr/bin/env python3
"""Generate the profile-scoped Mac ARM complex-product recipe after frozen FFT05."""
import argparse,base64,difflib,hashlib,json,subprocess,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
REV='79460ebecaa5625e57a5fb679a735659e73dc687'
PREFIX='third_party/blink/renderer/platform/'
FFT05_SHA='c94799b3993654fe2186471b22e73f2af8fb736ad9458d5abb0104cc45d98081'
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--scratch',type=Path,required=True);p.add_argument('--fft05',type=Path,required=True);a=p.parse_args();w=a.scratch.absolute();w.mkdir(parents=True,exist_ok=False)
 assert hashlib.sha256(a.fft05.read_bytes()).hexdigest()==FFT05_SHA
 subprocess.run(['git','init','--quiet',str(w)],check=True)
 paths=[PREFIX+'audio/fft_frame.cc',PREFIX+'audio/fft_frame.h',PREFIX+'BUILD.gn']
 for path in paths:
  data=base64.b64decode(urllib.request.urlopen(f'https://chromium.googlesource.com/chromium/src/+/{REV}/{path}?format=TEXT',timeout=60).read());t=w/path;t.parent.mkdir(parents=True,exist_ok=True);t.write_bytes(data)
 subprocess.run(['git','-C',str(w),'apply','--whitespace=error','--include='+PREFIX+'audio/fft_frame.cc',str(a.fft05.absolute())],check=True)
 before={path:(w/path).read_text() for path in paths};after=before.copy();s=after[paths[0]]
 s=s.replace('#include "base/check_op.h"','#include "base/check_op.h"\n#include "base/cpu.h"')
 s=s.replace('#include "third_party/blink/renderer/platform/audio/vector_math.h"','#include "third_party/blink/renderer/platform/audio/mac_arm_fft_multiply.h"\n#include "third_party/blink/renderer/platform/audio/vector_math.h"')
 anchor='  CHECK_GE(fft_size_, MinFFTSize());'
 assert s.count(anchor)==1
 s=s.replace(anchor,'''#if !BUILDFLAG(IS_MAC)
  const auto* multiply_profile = base::apostate::Profile::Get();
  if (multiply_profile && multiply_profile->ua_platform() == "macOS" &&
      multiply_profile->ua_architecture() == "arm") {
    mac_arm_multiply_ = mac_arm_fft_multiply::Portable;
#if defined(ARCH_CPU_X86_64)
    const auto& cpu = base::CPU::GetInstanceNoAllocation();
    if (cpu.has_avx() && cpu.has_fma3()) {
      mac_arm_multiply_ = mac_arm_fft_multiply::Fast;
    }
#endif
  }
#endif
'''+anchor)
 old='''  vector_math::Zvmul(real1.as_span(), imag1.as_span(), real2.as_span(),
                     imag2.as_span(), real1.as_span(), imag1.as_span(),
                     packed_size);

  // Multiply the packed DC/nyquist component
  real1[0] = real0 * real2[0];
  imag1[0] = imag0 * imag2[0];'''
 new='''  if (mac_arm_multiply_) {
    mac_arm_multiply_(real1.as_span(), imag1.as_span(), real2.as_span(),
                      imag2.as_span(), real1.as_span(), imag1.as_span());
    // Keep the packed endpoint operations after the complex product, including
    // when frame aliases this object.
    real1[0] = mac_arm_fft_multiply::MultiplyScalar(real0, real2[0]);
    imag1[0] = mac_arm_fft_multiply::MultiplyScalar(imag0, imag2[0]);
  } else {
    vector_math::Zvmul(real1.as_span(), imag1.as_span(), real2.as_span(),
                       imag2.as_span(), real1.as_span(), imag1.as_span(),
                       packed_size);

    // Multiply the packed DC/nyquist component
    real1[0] = real0 * real2[0];
    imag1[0] = imag0 * imag2[0];
  }'''
 assert s.count(old)==1;s=s.replace(old,new);after[paths[0]]=s
 h=after[paths[1]];anchor='  unsigned fft_size_ = 0;';assert h.count(anchor)==1;h=h.replace(anchor,'''  // Selected once from the existing immutable process profile. Native Mac
  // keeps vDSP; all other profiles keep the existing vector backend.
  using ComplexMultiply = void (*)(base::span<const float>,
                                  base::span<const float>,
                                  base::span<const float>,
                                  base::span<const float>,
                                  base::span<float>, base::span<float>);
  ComplexMultiply mac_arm_multiply_ = nullptr;

'''+anchor);after[paths[1]]=h
 g=after[paths[2]];anchor='    "audio/fft_frame.h",';assert g.count(anchor)==1;after[paths[2]]=g.replace(anchor,anchor+'\n    "audio/mac_arm_fft_multiply.h",')
 path=PREFIX+'audio/mac_arm_fft_multiply.h';before[path]='';after[path]=(ROOT/'scripts/fixtures/mac-arm-fft-multiply.h').read_text()
 patch='Subject: [PATCH] audio: reproduce Mac ARM complex-product arithmetic by profile\n\n'
 for path,s in after.items():
  patch+=f'diff --git a/{path} b/{path}\n'
  if not before[path]:patch+='new file mode 100644\n'
  patch+=''.join(difflib.unified_diff(before[path].splitlines(True),s.splitlines(True),fromfile='a/'+path if before[path] else '/dev/null',tofile='b/'+path))
 target=ROOT/'patches/0060-mac-arm-fft-multiply.patch';target.write_text(patch);(w/'0060.patch').write_text(patch)
 subprocess.run(['git','-C',str(w),'apply','--check','--whitespace=error','0060.patch'],check=True);subprocess.run(['git','-C',str(w),'apply','--whitespace=error','0060.patch'],check=True)
 for path,s in after.items():assert(w/path).read_text()==s
 (w/'candidate-manifest.json').write_text(json.dumps({'chromium_revision':REV,'fft05_sha256':FFT05_SHA,'patch_sha256':hashlib.sha256(patch.encode()).hexdigest(),'files':{f:{'before_sha256':hashlib.sha256(before[f].encode()).hexdigest(),'after_sha256':hashlib.sha256(s.encode()).hexdigest()}for f,s in after.items()},'v0':'strict patch apply and exact readback pass','v1_v3':'pending'},indent=2)+'\n');print(target)
if __name__=='__main__':main()
