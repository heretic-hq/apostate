#!/usr/bin/env python3
"""Inspect fixed font bytes and variable-font advances without modifying fonts.

Advance sums are table diagnostics, not HarfBuzz or native Windows results.
The optional mixed-sample prediction assumes a one-em CJK fallback advance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import fontTools
from fontTools.ttLib import TTFont


def inspect(path):
    font=TTFont(path);cmap=font.getBestCmap();units=font['head'].unitsPerEm
    os2=font['OS/2'];hhea=font['hhea']
    axes={a.axisTag:{'minimum':a.minValue,'default':a.defaultValue,'maximum':a.maxValue}
          for a in font['fvar'].axes} if 'fvar' in font else {}
    def advances(location):
        glyphs=font.getGlyphSet(location=location)
        sums={name:sum(glyphs[cmap[ord(c)]].width for c in sample)
              for name,sample in [('metricLatin','HxpgÅ'),('detectLatin','mmmmmmmmmmlli')]}
        return {'axes':location,'designUnitSums':sums,
                'metricLatin40':sums['metricLatin']*40/units,
                'detectLatin72':sums['detectLatin']*72/units,
                'mixed40AssumingOneEmFallback':sums['metricLatin']*40/units+40,
                'mixed72AssumingOneEmFallback':sums['detectLatin']*72/units+72}
    default={tag:a['default'] for tag,a in axes.items()}
    rows=[advances(default)]
    for tag,values in [('wdth',[75,80,85,90,95,100]),('wght',[300,350,400,450,500])]:
        if tag not in axes:continue
        for value in values:
            if axes[tag]['minimum']<=value<=axes[tag]['maximum']:
                rows.append(advances(dict(default,**{tag:value})))
    result={'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
      'family':font['name'].getDebugName(1),'version':font['name'].getDebugName(5),'unitsPerEm':units,
      'widthClass':os2.usWidthClass,'weightClass':os2.usWeightClass,'fsSelection':os2.fsSelection,
      'useTypoMetrics':bool(os2.fsSelection&128),
      'hhea':[hhea.ascent,hhea.descent,hhea.lineGap],
      'typo':[os2.sTypoAscender,os2.sTypoDescender,os2.sTypoLineGap],
      'win':[os2.usWinAscent,os2.usWinDescent],
      'mvarTags':[r.ValueTag for r in font['MVAR'].table.ValueRecord] if 'MVAR' in font else [],
      'axes':axes,'advanceControls':rows}
    font.close();return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font',type=Path,action='append',required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    data={'diagnostic_only':True,'corpus_admission':False,'fontTools':fontTools.__version__,
          'fonts':[inspect(p) for p in args.font]}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open('x') as output:json.dump(data,output,indent=2);output.write('\n')
    print(args.out)

if __name__=='__main__':main()
