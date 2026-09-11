#!/usr/bin/env python3
"""Compile/run ordered native FP controls outside Chromium; diagnostic only."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import platform

ROOT=Path(__file__).resolve().parents[1]
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--source',type=Path,default=ROOT/'scripts/fixtures/audio-fp-primitive-controls.rs')
    ap.add_argument('--rustc',default='rustc')
    args=ap.parse_args()
    out=args.out.absolute();out.mkdir(parents=True,exist_ok=False)
    source=out/'control.rs';shutil.copyfile(args.source,source)
    rustc=shutil.which(args.rustc) or args.rustc
    binary=out/'control'
    command=[str(rustc),'--edition=2021','-O',str(source),'-o',str(binary)]
    receipt={'diagnostic_only':True,'corpus_admission':False,'source_sha256':sha(source),
             'compiler':subprocess.check_output([rustc,'-vV'],text=True),
             'platform':platform.platform(),'command':command}
    try:
        with (out/'build.log').open('w') as log:
            subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
        subprocess.run([str(binary),str(out/'results.csv')],check=True)
        receipt.update(status='completed',binary_sha256=sha(binary),result_sha256=sha(out/'results.csv'))
    except Exception as error:
        receipt.update(status='failed',error=str(error));raise
    finally:
        (out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(out)
if __name__=='__main__':main()
