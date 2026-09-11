#!/usr/bin/env python3
"""Use the exact production-candidate math header in standalone compressor controls."""
import argparse,hashlib,json
from pathlib import Path
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--baseline',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--work',type=Path,required=True);p.add_argument('--portable',action='store_true');a=p.parse_args();m=json.loads((a.baseline/'manifest.json').read_text());a.work.mkdir(parents=True,exist_ok=False)
 for f,h in m['files'].items():
  data=(a.baseline/f).read_bytes();assert hashlib.sha256(data).hexdigest()==h;t=a.work/f;t.parent.mkdir(parents=True,exist_ok=True);t.write_bytes(data)
 cm=json.loads((a.candidate/'candidate-manifest.json').read_text());path='third_party/blink/renderer/platform/audio/mac_arm_audio_math.h';data=(a.candidate/path).read_bytes();assert hashlib.sha256(data).hexdigest()==cm['files'][path]['after_sha256'];(a.work/'candidate_math.h').write_bytes(data)
 (a.work/'build/build_config.h').write_text('''#pragma once
#if defined(__aarch64__)
#define ARCH_CPU_ARM64
#define ARCH_CPU_ARM_FAMILY
#elif defined(__x86_64__)
#define ARCH_CPU_X86_64
#define ARCH_CPU_X86_FAMILY
#endif
''')
 h=a.work/'adapters.h';s=h.read_text().replace('#include "compressor-mac-arm-log10.h"','').replace('#include "compressor-mac-arm-exp10.h"','');s=s.replace('#include "third_party/fdlibm/ieee754.h"','#include "third_party/fdlibm/ieee754.h"\n#include "candidate_math.h"')
 selector='''inline auto SelectCandidateFunctions() {
#if defined(ARCH_CPU_X86_64)
  if (__builtin_cpu_supports("fma"))
    return blink::mac_arm_audio_math::FmaFunctions();
#endif
  return blink::mac_arm_audio_math::PortableFunctions();
}
inline const auto candidate_functions = SelectCandidateFunctions();
'''
 if a.portable:selector='inline const auto candidate_functions = blink::mac_arm_audio_math::PortableFunctions();\n'
 s=s.replace('struct MathRecord',selector+'struct MathRecord');s=s.replace('mac_arm_log10::Evaluate(a)','candidate_functions.log10(a)').replace('mac_arm_exp10::Evaluate(b)','candidate_functions.exp10(b)');h.write_text(s)
 m['candidate_patch_sha256']=cm['patch_sha256'];m['experiment']='exact0055 math header; '+('generic functions' if a.portable else 'runtime CPU-selected functions');m['files']={str(p.relative_to(a.work)):hashlib.sha256(p.read_bytes()).hexdigest() for p in a.work.rglob('*') if p.is_file()};(a.work/'manifest.json').write_text(json.dumps(m,indent=2)+'\n');print(a.work)
