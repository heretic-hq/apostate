#!/usr/bin/env python3
"""Prepare a standalone single-operation compressor experiment from frozen sources."""
import argparse,hashlib,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--baseline',type=Path,required=True);p.add_argument('--work',type=Path,required=True);a=p.parse_args();m=json.loads((a.baseline/'manifest.json').read_text());a.work.mkdir(parents=True,exist_ok=False)
 for f,h in m['files'].items():
  data=(a.baseline/f).read_bytes();assert hashlib.sha256(data).hexdigest()==h;t=a.work/f;t.parent.mkdir(parents=True,exist_ok=True);t.write_bytes(data)
 p=a.work/'adapters.h';s=p.read_text();anchor='float r=::log10f(a);';assert s.count(anchor)==1;s=s.replace(anchor,'float r=mac_arm_log10::Evaluate(a);');s=s.replace('#pragma once','#pragma once\n#include "compressor-mac-arm-log10.h"',1);p.write_text(s)
 shutil.copyfile(ROOT/'scripts/fixtures/compressor-mac-arm-log10.h',a.work/'compressor-mac-arm-log10.h')
 m['baseline_manifest_sha256']=hashlib.sha256((a.baseline/'manifest.json').read_bytes()).hexdigest();m['experiment']='only native log10f replaced by documented observed Mac ARM arithmetic model';m['files']={str(p.relative_to(a.work)):hashlib.sha256(p.read_bytes()).hexdigest() for p in a.work.rglob('*') if p.is_file()};(a.work/'manifest.json').write_text(json.dumps(m,indent=2)+'\n');print(a.work)
