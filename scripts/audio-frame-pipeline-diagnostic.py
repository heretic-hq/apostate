#!/usr/bin/env python3
"""Isolated real-FFT wrapper -> complex multiply -> inverse controls using fast05."""
import argparse,base64,hashlib,json,os,platform,shutil,struct,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def prepare(a):
 w=a.work;w.mkdir(parents=True,exist_ok=False);base=json.loads((a.baseline/'source-manifest.json').read_text())
 for f,h in base['prepared_files_sha256'].items():
  data=(a.baseline/f).read_bytes();assert hashlib.sha256(data).hexdigest()==h;p=w/f;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
 shutil.copyfile(a.baseline/'Cargo.lock',w/'Cargo.lock');shutil.copyfile(ROOT/'scripts/fixtures/audio-frame-pipeline.rs',w/'src/main.rs');shutil.copyfile(ROOT/'scripts/fixtures/audio-frame-multiply.cc',w/'multiply.cc')
 r=json.loads((a.browser/'result.json').read_text());case=next(x for x in r['results'] if x['config']['id']=='convolver_finite_control');signal=base64.b64decode(case['inputs']['signal']['base64']);ir=base64.b64decode(case['inputs']['ir']['base64']);cases=[]
 for n,offset in [(1024,512),(2048,1024)]:
  name=f'browser-finite-{n}';p=w/'inputs'/name;p.mkdir(parents=True);(p/'input').write_bytes(signal[:n//2*4]+bytes(n//2*4));(p/'kernel').write_bytes(ir[offset*4:(offset+n//2)*4]+bytes(n//2*4));cases.append({'id':name,'fft_size':n,'browser_start':512 if n==1024 else None,'browser_frames':512 if n==1024 else None})
 seed=0x943abc71
 for n in [32,128,512,4096]:
  name=f'heldout-{n}';p=w/'inputs'/name;p.mkdir(parents=True)
  for role in ['input','kernel']:
   values=[]
   for _ in range(n):
    seed^=(seed<<13)&0xffffffff;seed^=seed>>17;seed^=(seed<<5)&0xffffffff;seed&=0xffffffff;values.append(((seed&0xffff)-32768)/32768)
   (p/role).write_bytes(struct.pack('<'+'f'*n,*values))
  cases.append({'id':name,'fft_size':n})
 manifest={'diagnostic_only':True,'not_v1':True,'fp_modes':{'kernel_forward':'default','signal_forward':'audio FTZ','multiply':'audio FTZ','inverse':'audio FTZ'},'baseline_manifest_sha256':hashlib.sha256((a.baseline/'source-manifest.json').read_bytes()).hexdigest(),'cases':cases,'files':{str(p.relative_to(w)):hashlib.sha256(p.read_bytes()).hexdigest()for p in w.rglob('*')if p.is_file()}};(w/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')

def run(a):
 w=a.work;manifest=json.loads((w/'manifest.json').read_text())
 for f,h in manifest['files'].items():assert hashlib.sha256((w/f).read_bytes()).hexdigest()==h
 out=w/'results';out.mkdir(exist_ok=False);env=os.environ.copy();env['RUSTC']=a.rustc
 reused=None
 if a.reuse_rust:
  prior=json.loads((a.reuse_rust/'manifest.json').read_text())
  for path,h in manifest['files'].items():
   if path.startswith(('src/','vendor/')) or path in ['Cargo.toml','Cargo.lock']:
    assert prior['files'].get(path)==h
  binary=a.reuse_rust/'target/release/apostate-audio-stages';dest=w/'target/release/apostate-audio-stages';dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(binary,dest);dest.chmod(0o755)
  reused={'source':str(binary),'sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'matched_rust_source_manifest':str(a.reuse_rust/'manifest.json')}
 else:
  with (w/'cargo.log').open('w')as log:subprocess.run([a.cargo,'build','--release','--offline','--jobs','2'],cwd=w,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
 command=[a.cxx,'-std=c++20','-O2','-ffp-contract=off','-I'+str(w),str(w/'multiply.cc'),'-o',str(w/'multiply')]
 if platform.system()=='Darwin':command+=['-framework','Accelerate']
 with (w/'cxx.log').open('w')as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
 fft=w/'target/release/apostate-audio-stages'
 for c in manifest['cases']:
  p=out/c['id'];p.mkdir();n=str(c['fft_size'])
  for role in ['input','kernel']:subprocess.run([str(fft),'forward-kernel' if role=='kernel' else 'forward-audio',n,str(w/'inputs'/c['id']/role),str(p/(role+'.fft'))],check=True)
  for mode in ['native','fma']:
   for alias in ['first','none','second','self']:
    name=mode+'-'+alias;product=p/(name+'.fft');subprocess.run([str(w/'multiply'),str(p/'input.fft'),str(p/'kernel.fft'),str(product),mode,alias],check=True);subprocess.run([str(fft),'inverse',n,str(product),str(p/(name+'.ifft'))],check=True)
 receipt={'diagnostic_only':True,'not_v1':True,'machine':platform.machine(),'reused_rust':reused,'rustc':subprocess.check_output([a.rustc,'--version','--verbose'],text=True),'cxx':subprocess.check_output([a.cxx,'--version'],text=True),'manifest_sha256':hashlib.sha256((w/'manifest.json').read_bytes()).hexdigest(),'outputs':{str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest()for p in out.rglob('*')if p.is_file()}};(w/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--work',type=Path,required=True);p.add_argument('--baseline',type=Path);p.add_argument('--browser',type=Path);p.add_argument('--prepare',action='store_true');p.add_argument('--run',action='store_true');p.add_argument('--cargo',default='cargo');p.add_argument('--rustc',default='rustc');p.add_argument('--cxx',default='clang++');p.add_argument('--reuse-rust',type=Path);a=p.parse_args();a.work=a.work.absolute()
 if a.prepare:prepare(a)
 if a.run:run(a)
 print(a.work)
