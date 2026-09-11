#!/usr/bin/env python3
"""Generate the scoped Mac ARM compressor arithmetic candidate from pinned source."""
import argparse,base64,difflib,hashlib,json,re,subprocess,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
REV='79460ebecaa5625e57a5fb679a735659e73dc687'
AUDIO='third_party/blink/renderer/platform/audio/'

def fetch(path):
 return base64.b64decode(urllib.request.urlopen(f'https://chromium.googlesource.com/chromium/src/+/{REV}/{path}?format=TEXT',timeout=60).read()).decode()

def math_header():
 log=(ROOT/'scripts/fixtures/compressor-mac-arm-log10.h').read_text()
 exp=(ROOT/'scripts/fixtures/compressor-mac-arm-exp10.h').read_text()
 log_data=log.split('static constexpr Entry table[]={',1)[1].split(' };',1)[0]
 log_rows=re.findall(r'\{([^,]+),([^}]+)\}',log_data)
 exp_data=exp.split('static constexpr double table[]={',1)[1].split(' };',1)[0]
 exp_rows=[line.strip().rstrip(',') for line in exp_data.splitlines() if line.strip()]
 assert len(log_rows)==65 and len(exp_rows)==128
 result=(ROOT/'scripts/fixtures/mac-arm-audio-math.h.in').read_text()
 result=result.replace('@LOG_TABLE@','\n'.join('      {'+a+', '+b+'},' for a,b in log_rows))
 result=result.replace('@EXP_TABLE@','\n'.join('      '+x+',' for x in exp_rows))
 assert '@LOG_TABLE@' not in result and '@EXP_TABLE@' not in result
 for constant in ['-0x1.bcbea4c258daap-4','0x1.287d3dcaf476dp-3','-0x1.bcb7b14dcbd85p-3','0x1.bcb7b151bc6b3p-2','0x1.34413509f79ffp-2']:
  assert constant in log and constant in result
 for constant in ['0x1.a934f0979a371p+8','0x1.ebfbdff30d656p-17','0x1.62e4453e10daep-8']:
  assert constant in exp and constant in result
 return result

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--scratch',type=Path,required=True);a=p.parse_args();scratch=a.scratch.absolute();scratch.mkdir(parents=True,exist_ok=False);subprocess.run(['git','init','--quiet',str(scratch)],check=True)
 paths=[AUDIO+'dynamics_compressor.cc',AUDIO+'dynamics_compressor.h','third_party/blink/renderer/platform/BUILD.gn']
 before={f:fetch(f) for f in paths};after=before.copy();cc=after[paths[0]]
 cc=cc.replace('#include "base/compiler_specific.h"','#include "base/apostate/profile.h"\n#include "base/compiler_specific.h"')
 cc=cc.replace('#include "base/containers/span.h"','#include "base/containers/span.h"\n#include "base/cpu.h"')
 cc=cc.replace('#include "base/notreached.h"','#include "base/notreached.h"\n#include "build/build_config.h"')
 cc=cc.replace('#include "third_party/blink/renderer/platform/audio/denormal_disabler.h"','#include "third_party/blink/renderer/platform/audio/denormal_disabler.h"\n#include "third_party/blink/renderer/platform/audio/mac_arm_audio_math.h"')
 anchor='  InitializeParameters();\n}'
 assert cc.count(anchor)==1
 cc=cc.replace(anchor,'''  InitializeParameters();
  const auto* profile = base::apostate::Profile::Get();
  if (profile && profile->ua_platform() == "macOS" &&
      profile->ua_architecture() == "arm") {
    auto functions = mac_arm_audio_math::PortableFunctions();
#if defined(ARCH_CPU_X86_64)
    const auto& cpu = base::CPU::GetInstanceNoAllocation();
    if (cpu.has_avx() && cpu.has_fma3()) {
      functions = mac_arm_audio_math::FmaFunctions();
    }
#endif
    mac_arm_log10_ = functions.log10;
    mac_arm_exp10_ = functions.exp10;
  }
}''')
 cc=cc.replace('audio_utilities::LinearToDecibels(', 'LinearToDecibels(').replace('audio_utilities::DecibelsToLinear(', 'DecibelsToLinear(')
 anchor='void DynamicsCompressor::Process('
 assert cc.count(anchor)==1
 cc=cc.replace(anchor,'''float DynamicsCompressor::LinearToDecibels(float value) const {
  return mac_arm_log10_ ? 20 * mac_arm_log10_(value)
                       : audio_utilities::LinearToDecibels(value);
}

float DynamicsCompressor::DecibelsToLinear(float value) const {
  return mac_arm_exp10_ ? mac_arm_exp10_(0.05f * value)
                       : audio_utilities::DecibelsToLinear(value);
}

'''+anchor)
 after[paths[0]]=cc
 h=after[paths[1]];anchor=' protected:\n';assert h.count(anchor)==1;h=h.replace(anchor,anchor+'''  float LinearToDecibels(float) const;
  float DecibelsToLinear(float) const;

  // Bound once at construction from the existing immutable profile loader.
  // Null keeps the original native audio utilities for other profiles.
  using MacArmMathFunction = float (*)(float);
  MacArmMathFunction mac_arm_log10_ = nullptr;
  MacArmMathFunction mac_arm_exp10_ = nullptr;

''');after[paths[1]]=h
 gn=after[paths[2]];anchor='    "audio/dynamics_compressor.h",\n';assert gn.count(anchor)==1;after[paths[2]]=gn.replace(anchor,anchor+'    "audio/mac_arm_audio_math.h",\n')
 after[AUDIO+'mac_arm_audio_math.h']=math_header()
 patch='Subject: [PATCH] audio: select Mac ARM compressor scalar math from the profile\n\n'
 for path,new in after.items():
  old=before.get(path,'');p=scratch/path;p.parent.mkdir(parents=True,exist_ok=True)
  if old:p.write_text(old)
  patch+=f'diff --git a/{path} b/{path}\n'
  if not old:patch+='new file mode 100644\n'
  patch+=''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/'+path if old else '/dev/null',tofile='b/'+path))
 dest=ROOT/'patches/0055-mac-arm-compressor-math.patch';dest.write_text(patch);(scratch/'0055.patch').write_text(patch)
 subprocess.run(['git','-C',str(scratch),'apply','--check','--whitespace=error','0055.patch'],check=True);subprocess.run(['git','-C',str(scratch),'apply','--whitespace=error','0055.patch'],check=True)
 for f,s in after.items():assert (scratch/f).read_text()==s
 (scratch/'candidate-manifest.json').write_text(json.dumps({'chromium_revision':REV,'patch_sha256':hashlib.sha256(patch.encode()).hexdigest(),'files':{f:{'before_sha256':hashlib.sha256(before.get(f,'').encode()).hexdigest(),'after_sha256':hashlib.sha256(s.encode()).hexdigest()} for f,s in after.items()},'v0':'strict patch application and exact readback pass','v1_v3':'pending parent build and browser runs'},indent=2)+'\n');print(dest)
if __name__=='__main__':main()
