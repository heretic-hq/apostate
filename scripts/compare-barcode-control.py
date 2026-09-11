#!/usr/bin/env python3
"""Compare captured native results with a standalone reader on identical pixels.

Diagnostics only. This does not admit either result as a reference capture.
"""
import argparse
import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess


FORMATS = {'aztec':'Aztec', 'code_128':'Code128', 'code_39':'Code39',
           'code_93':'Code93', 'codabar':'Codabar', 'data_matrix':'DataMatrix',
           'ean_13':'EAN13', 'ean_8':'EAN8', 'itf':'ITF', 'pdf417':'PDF417',
           'qr_code':'QRCode', 'upc_a':'UPCA', 'upc_e':'UPCE'}


def key(barcode):
    return barcode['rawValue'], barcode['format']


def normalize(barcode):
    name = ''.join(c.lower() for c in barcode['zxingFormat'] if c.isalnum())
    names = {''.join(c.lower() for c in v if c.isalnum()): k for k,v in FORMATS.items()}
    names.update(qrcodemodel2='qr_code', qrcodemodel1='qr_code', azteccode='aztec',
                 code39standard='code_39', code39extended='code_39', itf14='itf')
    return {'rawValue':barcode['rawValue'], 'format':names.get(name,'unmapped:'+barcode['zxingFormat']),
            'boundingBox':barcode['boundingBox'], 'cornerPoints':barcode['cornerPoints']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native',type=Path,required=True)
    parser.add_argument('--reader',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    native=json.loads(args.native.read_text())
    if native.get('error') or not native.get('barcodeDetectorPresent'):
        raise ValueError('native capture did not complete')
    formats=','.join(FORMATS[name] for name in native['supportedFormats'])
    rows=[]
    for scene in native['scenes']:
        name=scene['name']
        if name != Path(name).name or name in {'.','..'}:
            raise ValueError('invalid scene name')
        pixels=base64.b64decode(scene['grayBase64'],validate=True)
        if len(pixels)!=scene['width']*scene['height'] or hashlib.sha256(pixels).hexdigest()!=scene['graySha256']:
            raise ValueError('pixel receipt mismatch: '+name)
        path=args.out/(name+'.gray');path.write_bytes(pixels)
        command=[str(args.reader.resolve()),str(path.resolve()),str(scene['width']),str(scene['height']),formats]
        repeats=[]
        for _ in range(3):
            result=subprocess.run(command,capture_output=True,text=True,check=True,timeout=20)
            repeats.append(json.loads(result.stdout))
        zxing=[normalize(b) for b in repeats[0]]
        expected=scene['native']
        geometry=[]
        for n in expected:
            matches=[b for b in zxing if key(b)==key(n)]
            if len(matches)==1:
                z=matches[0]
                geometry.append({'payload':n['rawValue'],'format':n['format'],
                    'boundingBoxExact':n['boundingBox']==z['boundingBox'],
                    'cornerPointsExact':n['cornerPoints']==z['cornerPoints'],
                    'boundingBoxDeltas':{k:z['boundingBox'][k]-n['boundingBox'][k] for k in n['boundingBox']},
                    'native':n,'zxing':z})
        rows.append({'scene':name,'graySha256':scene['graySha256'],'width':scene['width'],'height':scene['height'],
            'nativeRepeatStable':scene['repeatStable'],'zxingRepeatStable':all(r==repeats[0] for r in repeats),
            'nativeCount':len(expected),'zxingCount':len(zxing),
            'payloadAndFormatMultisetExact':Counter(map(key,expected))==Counter(map(key,zxing)),
            'payloadAndFormatOrderExact':list(map(key,expected))==list(map(key,zxing)),
            'allFieldsExact':expected==zxing,'geometry':geometry,'native':expected,'zxing':zxing,
            'zxingRawRepeats':repeats,'command':command})
    summary={'diagnostic_only':True,'corpus_admission':False,'nativeSupportedFormats':native['supportedFormats'],
        'zxingRequestedFormats':formats,'nativeJsonSha256':hashlib.sha256(args.native.read_bytes()).hexdigest(),
        'readerSha256':hashlib.sha256(args.reader.read_bytes()).hexdigest(),
        'sceneCount':len(rows),'nativeRepeatStable':all(r['nativeRepeatStable'] for r in rows),
        'zxingRepeatStable':all(r['zxingRepeatStable'] for r in rows),
        'payloadAndFormatMultisetExact':sum(r['payloadAndFormatMultisetExact'] for r in rows),
        'payloadAndFormatOrderExact':sum(r['payloadAndFormatOrderExact'] for r in rows),
        'allFieldsExact':sum(r['allFieldsExact'] for r in rows),
        'matchedBarcodes':sum(len(r['geometry']) for r in rows),
        'boundingBoxesExact':sum(g['boundingBoxExact'] for r in rows for g in r['geometry']),
        'cornerPointsExact':sum(g['cornerPointsExact'] for r in rows for g in r['geometry'])}
    (args.out/'comparison.json').write_text(json.dumps({'summary':summary,'scenes':rows},indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    main()
