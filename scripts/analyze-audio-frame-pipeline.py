#!/usr/bin/env python3
"""Validate stage hashes and compare Mac native versus Linux target complex products."""
import argparse,base64,hashlib,json,struct
from pathlib import Path

def load(root):
 receipt=json.loads((root/'receipt.json').read_text());manifest=json.loads((root/'manifest.json').read_text());assert hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest()==receipt['manifest_sha256']
 for f,h in receipt['outputs'].items():assert hashlib.sha256((root/'results'/f).read_bytes()).hexdigest()==h
 return manifest

def diff(a,b):
 assert len(a)==len(b);ids=[i//4 for i in range(0,len(a),4)if a[i:i+4]!=b[i:i+4]];return {'different':len(ids),'values':len(a)//4,'first':ids[0]if ids else None}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('mac',type=Path);p.add_argument('linux',type=Path);p.add_argument('--browser',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();m=load(a.mac);n=load(a.linux);assert m==n
 results=[]
 for c in m['cases']:
  x=a.mac/'results'/c['id'];y=a.linux/'results'/c['id'];row={'id':c['id'],'forward':{role:diff((x/(role+'.fft')).read_bytes(),(y/(role+'.fft')).read_bytes())for role in ['input','kernel']},'products':{},'inverse':{}}
  for alias in ['first','none','second','self']:
   for suffix in ['.fft.complex','.fft']:row['products'][alias+suffix]=diff((x/('native-'+alias+suffix)).read_bytes(),(y/('fma-'+alias+suffix)).read_bytes())
   row['inverse'][alias]=diff((x/('native-'+alias+'.ifft')).read_bytes(),(y/('fma-'+alias+'.ifft')).read_bytes())
  if c.get('browser_start') is not None:
   r=json.loads((a.browser/'result.json').read_text());case=next(t for t in r['results']if t['config']['id']=='convolver_finite_control');raw=base64.b64decode(case['output']['base64']);start=c['browser_start']*4;size=c['browser_frames']*4;expected=raw[start:start+size]
   row['browser_prefix']={'mac_native':diff(expected,(x/'native-first.ifft').read_bytes()[:size]),'linux_target':diff(expected,(y/'fma-first.ifft').read_bytes()[:size]),'linux_native':diff(expected,(y/'native-first.ifft').read_bytes()[:size])}
  row['alias_raw_product']={side:{alias:diff((root/'native-first.fft.complex').read_bytes(),(root/('native-'+alias+'.fft.complex')).read_bytes())for alias in ['none','second']}for side,root in [('mac',x),('linux',y)]}
  results.append(row)
 report={'diagnostic_only':True,'not_v1':True,'cases':results,'note':'Post-product DC/Nyquist assignments retain original FFTFrame ordering. Self and output-second alias post-DC results are compared to that ordering, not silently corrected.'};a.out.write_text(json.dumps(report,indent=2)+'\n');print(a.out)
if __name__=='__main__':main()
