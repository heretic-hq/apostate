#!/usr/bin/env python3
"""Generate Mac ARM vector table-selection arithmetic for PeriodicWave."""
import argparse,base64,difflib,hashlib,json,subprocess,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
REV='79460ebecaa5625e57a5fb679a735659e73dc687'
PREFIX='third_party/blink/renderer/modules/webaudio/'

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--scratch',type=Path,required=True);a=p.parse_args();w=a.scratch.absolute();w.mkdir(parents=True,exist_ok=False);subprocess.run(['git','init','--quiet',str(w)],check=True)
 before={}
 for name in ['periodic_wave.cc','periodic_wave.h']:
  path=PREFIX+name;before[path]=base64.b64decode(urllib.request.urlopen(f'https://chromium.googlesource.com/chromium/src/+/{REV}/{path}?format=TEXT',timeout=60).read()).decode()
 after=before.copy();s=after[PREFIX+'periodic_wave.cc']
 anchor='#include "base/compiler_specific.h"';assert s.count(anchor)==1;s=s.replace(anchor,'#include "base/apostate/profile.h"\n'+anchor)
 anchor='    : sample_rate_(sample_rate), cents_per_range_(kCentsPerRange) {\n';assert s.count(anchor)==1;s=s.replace(anchor,anchor+'''  const auto* profile = base::apostate::Profile::Get();
  use_mac_arm_vector_math_ = profile && profile->ua_platform() == "macOS" &&
                            profile->ua_architecture() == "arm";
''')
 old='''  __m128 v_ratio =
      _mm_div_ps(frequency, _mm_set1_ps(lowest_fundamental_frequency_));'''
 new='''  // NEON's vector recipe multiplies by a rounded reciprocal. Preserve both
  // rounding steps for the Mac ARM profile; scalar playback keeps its recipe.
  __m128 v_ratio;
  if (use_mac_arm_vector_math_) {
    v_ratio = _mm_mul_ps(
        frequency, _mm_set1_ps(1 / lowest_fundamental_frequency_));
  } else {
    v_ratio =
        _mm_div_ps(frequency, _mm_set1_ps(lowest_fundamental_frequency_));
  }'''
 assert s.count(old)==1;s=s.replace(old,new)
 old='''  __m128 v_pitch_range =
      _mm_add_ps(_mm_set1_ps(1.0),
                 _mm_div_ps(_mm_load_ps(cents_above_lowest_frequency.data()),
                            _mm_set1_ps((cents_per_range_))));'''
 new='''  const __m128 cents = _mm_load_ps(cents_above_lowest_frequency.data());
  const __m128 scaled_cents =
      use_mac_arm_vector_math_
          ? _mm_mul_ps(cents, _mm_set1_ps(1 / cents_per_range_))
          : _mm_div_ps(cents, _mm_set1_ps(cents_per_range_));
  __m128 v_pitch_range = _mm_add_ps(_mm_set1_ps(1.0), scaled_cents);'''
 assert s.count(old)==1;s=s.replace(old,new);after[PREFIX+'periodic_wave.cc']=s
 h=after[PREFIX+'periodic_wave.h'];anchor='  float rate_scale_;\n';assert h.count(anchor)==1;h=h.replace(anchor,anchor+'''
  // Bound at construction from the existing immutable process profile.
  bool use_mac_arm_vector_math_ = false;
''');after[PREFIX+'periodic_wave.h']=h
 patch='Subject: [PATCH] audio: match Mac ARM vector wave-table range arithmetic\n\n'
 for path,s in after.items():
  target=w/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(before[path]);patch+=f'diff --git a/{path} b/{path}\n';patch+=''.join(difflib.unified_diff(before[path].splitlines(True),s.splitlines(True),fromfile='a/'+path,tofile='b/'+path))
 target=ROOT/'patches/0059-mac-arm-oscillator-vector-math.patch';target.write_text(patch);(w/'0059.patch').write_text(patch)
 subprocess.run(['git','-C',str(w),'apply','--check','--whitespace=error','0059.patch'],check=True);subprocess.run(['git','-C',str(w),'apply','--whitespace=error','0059.patch'],check=True)
 for path,s in after.items():assert (w/path).read_text()==s
 (w/'candidate-manifest.json').write_text(json.dumps({'chromium_revision':REV,'patch_sha256':hashlib.sha256(patch.encode()).hexdigest(),'files':{f:{'before_sha256':hashlib.sha256(before[f].encode()).hexdigest(),'after_sha256':hashlib.sha256(s.encode()).hexdigest()} for f,s in after.items()},'v0':'strict apply and source readback pass','v1_v3':'pending parent'},indent=2)+'\n');print(target)
if __name__=='__main__':main()
