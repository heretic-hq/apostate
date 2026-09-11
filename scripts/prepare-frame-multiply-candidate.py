#!/usr/bin/env python3
"""Prepare the exact target complex multiplier in frozen frame-pipeline controls."""
import argparse,hashlib,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--baseline',type=Path,required=True);p.add_argument('--work',type=Path,required=True);a=p.parse_args();m=json.loads((a.baseline/'manifest.json').read_text());a.work.mkdir(parents=True,exist_ok=False)
 for f,h in m['files'].items():
  data=(a.baseline/f).read_bytes();assert hashlib.sha256(data).hexdigest()==h;t=a.work/f;t.parent.mkdir(parents=True,exist_ok=True);t.write_bytes(data)
 for path,text in {'base/compiler_specific.h':'#pragma once\n#define ALWAYS_INLINE inline __attribute__((always_inline))\n','base/containers/span.h':'#pragma once\n#include <span>\nnamespace base {template<class T>using span=std::span<T>;}\n','build/build_config.h':'#pragma once\n#if defined(__aarch64__)\n#define ARCH_CPU_ARM64\n#define ARCH_CPU_ARM_FAMILY\n#elif defined(__x86_64__)\n#define ARCH_CPU_X86_64\n#define ARCH_CPU_X86_FAMILY\n#endif\n'}.items():
  t=a.work/path;t.parent.mkdir(parents=True,exist_ok=True);t.write_text(text)
 shutil.copyfile(ROOT/'scripts/fixtures/mac-arm-fft-multiply.h',a.work/'mac-arm-fft-multiply.h')
 f=a.work/'multiply.cc';s=f.read_text();s='#include "mac-arm-fft-multiply.h"\n'+s
 old='  for(unsigned i=0;i<n;++i){float r1=ar[i],i1=ai[i],r2=br[i],i2=bi[i];rr[i]=std::fma(-i1,i2,r1*r2);ri[i]=std::fma(i1,r2,r1*i2);}'
 new='''  auto fn=blink::mac_arm_fft_multiply::Portable;
#if defined(ARCH_CPU_X86_64)
  if(__builtin_cpu_supports("avx")&&__builtin_cpu_supports("fma"))fn=blink::mac_arm_fft_multiply::Fast;
#endif
  fn(base::span<const float>(ar,n),base::span<const float>(ai,n),base::span<const float>(br,n),base::span<const float>(bi,n),base::span<float>(rr,n),base::span<float>(ri,n));'''
 assert s.count(old)==1;s=s.replace(old,new);s=s.replace(' rr[0]=real0*br[0];ri[0]=imag0*bi[0];',' if(mode=="fma"){rr[0]=blink::mac_arm_fft_multiply::MultiplyScalar(real0,br[0]);ri[0]=blink::mac_arm_fft_multiply::MultiplyScalar(imag0,bi[0]);}else{rr[0]=real0*br[0];ri[0]=imag0*bi[0];}');f.write_text(s);m['experiment']='CPU-selected target complex multiply with primitive ARM NaN policies';m['files']={str(p.relative_to(a.work)):hashlib.sha256(p.read_bytes()).hexdigest()for p in a.work.rglob('*')if p.is_file()};(a.work/'manifest.json').write_text(json.dumps(m,indent=2)+'\n');print(a.work)
