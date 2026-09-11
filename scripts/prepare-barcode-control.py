#!/usr/bin/env python3
"""Prepare pinned standalone barcode-control inputs in an isolated directory.

This builds only the diagnostic reader, with two compiler jobs. It does not
modify Chromium, launch a browser, or admit results into the corpus.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import tarfile
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
CHROMIUM='79460ebecaa5625e57a5fb679a735659e73dc687'
ZXING='287c85df6f961c8efbfb5ffd736cd9457b8b890e'
ARCHIVE_SHA='97d952c661b1f79d21aacc2ec544ef05c4d1465f55692cc49622ea6a8166ca7b'
FIXTURES={'qr_code':'0ee5b26e6b3f6c4cf76f4bab285e9864fae5d3e15ae14811200666914cba5b39',
          'ean_13':'f58e33d5cfa2f4be5ca3aaca6b7c724b012c89a387904d7b36ff0a39251b234e',
          'data_matrix':'51f09e07e69582857902218755e9068b539c70eb84c0ea77547e3cc7f0b007ae'}

def sha(data):return hashlib.sha256(data).hexdigest()

def fetch(path,url,expected,encoded=False):
    data=path.read_bytes() if path.exists() else urllib.request.urlopen(url,timeout=60).read()
    if encoded and not path.exists():data=base64.b64decode(data)
    if sha(data)!=expected:raise ValueError('pinned checksum mismatch: '+str(path))
    if not path.exists():path.write_bytes(data)
    return data

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();out=args.out.resolve();inputs=out/'inputs'
    if platform.system()!='Darwin' or platform.machine()!='arm64':
        raise ValueError('this control requires the local arm64 Mac toolchain')
    inputs.mkdir(parents=True,exist_ok=True)
    fixtures=[]
    for name,digest in FIXTURES.items():
        url=f'https://chromium.googlesource.com/chromium/src/+/{CHROMIUM}/services/test/data/{name}.png?format=TEXT'
        data=fetch(inputs/(name+'.png'),url,digest,True)
        fixtures.append({'name':name,'url':url,'sha256':digest,
                         'width':struct.unpack('>I',data[16:20])[0],'height':struct.unpack('>I',data[20:24])[0]})
    archive_url=f'https://codeload.github.com/zxing-cpp/zxing-cpp/tar.gz/{ZXING}'
    archive=fetch(inputs/'zxing.tar.gz',archive_url,ARCHIVE_SHA)
    dependency=out/'dependency';source=dependency/('zxing-cpp-'+ZXING)
    with tarfile.open(inputs/'zxing.tar.gz') as tar:
        if not source.exists():tar.extractall(dependency,filter='data')
        for member in tar:
            target=dependency/member.name
            if member.isfile() and sha(target.read_bytes())!=sha(tar.extractfile(member).read()):
                raise ValueError('extracted source changed: '+member.name)
            if member.issym() and (not target.is_symlink() or
                    os.path.normpath(os.readlink(target))!=os.path.normpath(member.linkname)):
                raise ValueError('extracted symlink changed: '+member.name)
    build=out/'zxing-build'
    commands=[['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Release',
        '-DCMAKE_C_COMPILER=/usr/bin/clang','-DCMAKE_CXX_COMPILER=/usr/bin/clang++','-DBUILD_SHARED_LIBS=OFF',
        '-DZXING_READERS=ON','-DZXING_WRITERS=OFF','-DZXING_C_API=OFF','-DZXING_EXAMPLES=OFF',
        '-DZXING_EXAMPLES_QT=OFF','-DZXING_BLACKBOX_TESTS=OFF','-DZXING_UNIT_TESTS=OFF','-DZXING_PYTHON_MODULE=OFF'],
        ['cmake','--build',str(build),'--parallel','2'],
        ['/usr/bin/clang++','-std=c++20','-O2','-I',str(source/'core/src'),'-I',str(build/'core'),
         str(ROOT/'scripts/fixtures/barcode-reader-control.cc'),str(build/'core/libZXing.a'),'-o',str(out/'reader')]]
    for command,name in zip(commands,['configure','build','reader-build']):
        with (out/(name+'.log')).open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    manifest={'fixtures':fixtures,'zxing':{'version':'3.1.1','revision':ZXING,'url':archive_url,
                                         'sha256':ARCHIVE_SHA,'archive_bytes':len(archive)}}
    (inputs/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    parameters={'fixtures':[dict(f,pngBase64=base64.b64encode((inputs/(f['name']+'.png')).read_bytes()).decode()) for f in fixtures]}
    (out/'parameters.json').write_text(json.dumps(parameters)+'\n')
    licenses=[source/'LICENSE',source/'core/src/libzueci/zueci.c',source/'core/src/libzueci/zueci.h']
    metadata={}
    for name,command in [('compiler',['/usr/bin/clang++','--version']),('cmake',['cmake','--version']),
                         ('os',['sw_vers']),('sdk',['xcrun','--show-sdk-version']),('reader_type',['file',str(out/'reader')])]:
        metadata[name]=subprocess.check_output(command,text=True).strip()
    receipt={'diagnostic_only':True,'corpus_admission':False,'source':manifest['zxing'],'commands':commands,
             'toolchain':metadata,'licenses':[{'path':str(p.relative_to(source)),'sha256':sha(p.read_bytes())} for p in licenses],
             'license_summary':'Apache-2.0 core; BSD-3-Clause libzueci with embedded permissive UTF-8 notice',
             'reader_source_sha256':sha((ROOT/'scripts/fixtures/barcode-reader-control.cc').read_bytes()),
             'library_sha256':sha((build/'core/libZXing.a').read_bytes()),'reader_sha256':sha((out/'reader').read_bytes()),
             'cmake_cache_sha256':sha((build/'CMakeCache.txt').read_bytes())}
    (out/'build-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({'out':str(out),'reader_sha256':receipt['reader_sha256'],'jobs':2}))

if __name__=='__main__':main()
