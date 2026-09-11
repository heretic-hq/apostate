#!/usr/bin/env python3
"""Compare full pinned compressor outputs, arithmetic stages and native math calls."""
import argparse,base64,hashlib,json,struct
from pathlib import Path

def load(root):
 receipt=json.loads((root/'receipt.json').read_text());manifest=json.loads((root/'manifest.json').read_text())
 assert hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest()==receipt['manifest_sha256']
 for f,h in receipt['results'].items():assert hashlib.sha256((root/'results'/f).read_bytes()).hexdigest()==h
 return receipt,manifest

def diff_float(a,b,width=4):
 assert len(a)==len(b) and len(a)%width==0
 indices=[i//width for i in range(0,len(a),width) if a[i:i+width]!=b[i:i+width]]
 out={'different_values':len(indices),'total_values':len(a)//width}
 if indices:
  i=indices[0];fmt='<f' if width==4 else '<d'
  out.update(first_index=i,first_left=struct.unpack(fmt,a[i*width:(i+1)*width])[0],first_right=struct.unpack(fmt,b[i*width:(i+1)*width])[0],left_bits=a[i*width:(i+1)*width].hex(),right_bits=b[i*width:(i+1)*width].hex())
 return out

def browser_check(root,browser):
 data=json.loads((browser/'result.json').read_text());rows=[]
 wanted={c['id'] for c in json.loads((root/'manifest.json').read_text())['cases']}
 for case in data['cases']:
  if case['id'] not in wanted:continue
  observed=(root/'results'/case['id']/'output.f32').read_bytes()
  expected=b''.join(base64.b64decode(c['base64']) for c in case['output'])
  row=diff_float(expected,observed[:len(expected)]);row['id']=case['id'];rows.append(row)
 assert {r['id'] for r in rows}==wanted
 return rows

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('left',type=Path);p.add_argument('right',type=Path);p.add_argument('--mac-browser',type=Path,required=True);p.add_argument('--linux-browser',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 lr,lm=load(a.left);rr,rm=load(a.right);assert lr['manifest_sha256']==rr['manifest_sha256']
 report={'diagnostic_only':True,'not_v1':True,'left_browser_reference':str(a.mac_browser),'right_browser_reference':str(a.linux_browser),'left_compiler':lr['compiler'],'right_compiler':rr['compiler'],'left_browser':browser_check(a.left,a.mac_browser),'right_browser':browser_check(a.right,a.linux_browser),'cases':[]}
 for case in lm['cases']:
  name=case['id'];left=a.left/'results'/name;right=a.right/'results'/name
  out=diff_float((left/'output.f32').read_bytes(),(right/'output.f32').read_bytes());stages=diff_float((left/'stages.f32').read_bytes(),(right/'stages.f32').read_bytes())
  if stages['different_values']:
   i=stages['first_index'];stages['first_frame']=i//len(lm['fields']);stages['first_field']=lm['fields'][i%len(lm['fields'])]
  mathleft=(left/'math.bin').read_bytes();mathright=(right/'math.bin').read_bytes();assert len(mathleft)==len(mathright)
  mismatches=[];independent=[]
  for n,(x,y) in enumerate(zip(struct.iter_unpack('<IIddd',mathleft),struct.iter_unpack('<IIddd',mathright))):
   assert x[:2]==y[:2]
   if mathleft[n*32:(n+1)*32]!=mathright[n*32:(n+1)*32]:
    row={'call':n,'operation':{1:'powf',2:'log10f',3:'exp',4:'sin'}[x[0]],'frame':x[1],'args_left':list(x[2:4]),'args_right':list(y[2:4]),'left_result':x[4],'right_result':y[4],'same_arguments':mathleft[n*32+8:n*32+24]==mathright[n*32+8:n*32+24]}
    mismatches.append(row)
    if row['same_arguments']:independent.append(row)
  report['cases'].append({'id':name,'output':out,'stages':stages,'math_calls':len(mathleft)//32,'different_math_calls':len(mismatches),'independent_math_differences':len(independent),'first_math_differences':mismatches[:8],'first_independent_math_differences':independent[:8]})
 a.out.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n');print(a.out)
if __name__=='__main__':main()
