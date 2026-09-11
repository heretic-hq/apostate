#!/usr/bin/env python3
"""Prepare/run an instrumented pinned compressor in isolated standalone adapters."""
import argparse,base64,hashlib,json,platform,re,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FIELDS=['desired_gain','scaled_desired_gain','is_releasing','db_compression_diff','envelope_rate','compressor_input','shaped_input','attenuation','db_attenuation','db_per_frame','sat_release_rate','rate','detector_average','compressor_gain','post_warp_compressor_gain','total_gain','output_left','k','linear_post_gain','db_max_attack_compression_diff']

def prepare(work,pristine,reference):
 work.mkdir(parents=True,exist_ok=False)
 for path in pristine.rglob('*'):
  if path.is_file():
   target=work/'pristine'/path.relative_to(pristine);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
 def read(f):return (pristine/f).read_text()
 audio='third_party/blink/renderer/platform/audio/'
 header=re.sub(r'^#include.*\n','',read(audio+'dynamics_compressor.h'),flags=re.M)
 source=re.sub(r'^#include.*\n','',read(audio+'dynamics_compressor.cc'),flags=re.M)
 source=source.replace('exp(static_cast<double>','ObservedExp(static_cast<double>').replace('sin(static_cast<double>','ObservedSin(static_cast<double>')
 anchor='      frame_index++;'
 assert source.count(anchor)==1
 source=source.replace(anchor,'      RecordStages({desired_gain, scaled_desired_gain, float(is_releasing), db_compression_diff, envelope_rate, compressor_input, shaped_input, attenuation, db_attenuation, db_per_frame, sat_release_rate, rate, detector_average, compressor_gain, post_warp_compressor_gain, total_gain, destination_bus->Channel(0)->Span()[frame_index], k, linear_post_gain, db_max_attack_compression_diff_});\n'+anchor)
 utils=read(audio+'audio_utilities.cc');start=utils.index('float DecibelsToLinear');end=utils.index('size_t TimeToSampleFrame');utils=utils[start:end].replace('return powf(', 'return ObservedPowf(').replace('20 * log10f(', '20 * ObservedLog10f(').replace('DCHECK_GE(linear, 0);','')
 combined='#include "adapters.h"\n'+header+'\n'+source+'\nnamespace blink::audio_utilities {\n'+utils+'}\n'+(ROOT/'scripts/fixtures/compressor-diagnostic-main.cc').read_text()
 (work/'compressor.cc').write_text(combined);shutil.copyfile(ROOT/'scripts/fixtures/compressor-diagnostic-adapters.h',work/'adapters.h')
 for f in ['ieee754.cc','ieee754.h','overflowing-math.h']:
  p=work/'third_party/fdlibm'/f;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(pristine/'third_party/fdlibm'/f,p)
 for f,s in {'base/bit_cast.h':'#include <bit>\n#include <cstdint>\nnamespace base {using std::bit_cast;}\n','base/compiler_specific.h':'#define ALWAYS_INLINE inline __attribute__((always_inline))\n','build/build_config.h':'#pragma once\n'}.items():
  p=work/f;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s)
 data=json.loads((reference/'result.json').read_text());cases=[]
 for case in data['cases']:
  if case['config']['source']!='pcm':continue
  p=work/'inputs'/case['id'];p.parent.mkdir(exist_ok=True);raw=b''.join(base64.b64decode(c['base64']) for c in case['inputs']);p.write_bytes(raw)
  cases.append({'id':case['id'],'config':case['config'],'frames':case['output'][0]['frames'],'input_sha256':hashlib.sha256(raw).hexdigest()})
 manifest={'diagnostic_only':True,'not_v1':True,'chromium_revision':'79460ebecaa5625e57a5fb679a735659e73dc687','fields':FIELDS,'math_record':'little endian u32 operation,u32 frame,f64 a,f64 b,f64 result; op1powf,2log10f,3exp,4sin','cases':cases,'files':{str(p.relative_to(work)):hashlib.sha256(p.read_bytes()).hexdigest() for p in work.rglob('*') if p.is_file()}}
 (work/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')

def run(work,cxx):
 manifest=json.loads((work/'manifest.json').read_text())
 for f,h in manifest['files'].items():assert hashlib.sha256((work/f).read_bytes()).hexdigest()==h
 output=work/'results';output.mkdir(exist_ok=False)
 command=[cxx,'-std=c++20','-O2','-ffp-contract=off','-I'+str(work),str(work/'compressor.cc'),str(work/'third_party/fdlibm/ieee754.cc'),'-o',str(work/'compressor')]
 compile_command=command.copy()
 with (work/'compile.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
 for case in manifest['cases']:
  dest=output/case['id'];dest.mkdir();c=case['config'];params=c['dynamics']
  command=[str(work/'compressor'),str(work/'inputs'/case['id']),str(dest),str(case['frames']),str(c.get('channels',1)),str(c['sampleRate'])]+[str(params[k]) for k in ['threshold','knee','ratio','attack','release']]
  subprocess.run(command,check=True)
 receipt={'diagnostic_only':True,'not_v1':True,'machine':platform.machine(),'compiler':subprocess.check_output([cxx,'--version'],text=True),'compile_command':compile_command,'manifest_sha256':hashlib.sha256((work/'manifest.json').read_bytes()).hexdigest(),'binary_sha256':hashlib.sha256((work/'compressor').read_bytes()).hexdigest(),'results':{str(p.relative_to(output)):hashlib.sha256(p.read_bytes()).hexdigest() for p in output.rglob('*') if p.is_file()}}
 (work/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--work',type=Path,required=True);p.add_argument('--prepare',action='store_true');p.add_argument('--run',action='store_true');p.add_argument('--pristine',type=Path);p.add_argument('--reference',type=Path);p.add_argument('--cxx',default='clang++');a=p.parse_args();w=a.work.absolute()
 if a.prepare:prepare(w,a.pristine,a.reference)
 if a.run:run(w,a.cxx)
 print(w)
