#!/usr/bin/env python3
"""Extend frozen compressor controls with captured oscillator PCM as input."""
import argparse,base64,hashlib,json
from pathlib import Path
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--baseline',type=Path,required=True);p.add_argument('--work',type=Path,required=True);p.add_argument('--browser',type=Path,required=True);a=p.parse_args();m=json.loads((a.baseline/'manifest.json').read_text());a.work.mkdir(parents=True,exist_ok=False)
 for f,h in m['files'].items():
  data=(a.baseline/f).read_bytes();assert hashlib.sha256(data).hexdigest()==h;t=a.work/f;t.parent.mkdir(parents=True,exist_ok=True);t.write_bytes(data)
 cases={c['id']:c for c in json.loads((a.browser/'result.json').read_text())['cases']}
 for oscillator,chain in [('original_oscillator','original_chain'),('saw_oscillator_heldout','saw_chain_heldout'),('triangle_oscillator_heldout','triangle_chain_heldout')]:
  data=b''.join(base64.b64decode(c['base64']) for c in cases[oscillator]['output']);(a.work/'inputs'/chain).write_bytes(data)
  config=cases[chain]['config'].copy();config['source']='pcm';config['diagnostic_input_from']=oscillator
  m['cases'].append({'id':chain,'config':config,'frames':cases[chain]['output'][0]['frames'],'input_sha256':hashlib.sha256(data).hexdigest()})
 m['oscillator_input_browser_fixture']='audio-dsp-controls.html frozen Mac .83';m['files']={str(p.relative_to(a.work)):hashlib.sha256(p.read_bytes()).hexdigest() for p in a.work.rglob('*') if p.is_file()};(a.work/'manifest.json').write_text(json.dumps(m,indent=2)+'\n');print(a.work)
