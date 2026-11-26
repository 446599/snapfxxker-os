#!/usr/bin/env python3
"""Restore the three static TTF faces the actual vendored CJK baker expects.

Noto Sans CJK SC / Source Han Sans SC are the same OFL font family. Use the
full, pinned upstream SC variable TrueType asset (not an unpinned download),
instantiate its wght axis, and let the vendored baker validate each glyph.
"""
from pathlib import Path
from hashlib import sha1, sha256
import json
import sys
import urllib.request
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

PIN='523d033d6cb47f4a80c58a35753646f5c3608a78'
URL=f'https://raw.githubusercontent.com/notofonts/noto-cjk/{PIN}/Sans/Variable/TTF/NotoSansCJKsc-VF.ttf'
BLOB='e67840913223f5c5db60570ce5bf001b0e079d42'
SIZE=36144788

def main(root):
    dest=root/'assets/fonts'
    source=dest/'NotoSansCJKsc-VF.ttf'
    if not source.exists():
        with urllib.request.urlopen(URL,timeout=120) as response:
            data=response.read(SIZE+1)
        if len(data)!=SIZE or sha1(b'blob '+str(SIZE).encode()+b'\0'+data).hexdigest()!=BLOB:
            raise ValueError('Pinned Noto variable font mismatch')
        source.write_bytes(data)
    else:
        data=source.read_bytes()
        if len(data)!=SIZE or sha1(b'blob '+str(SIZE).encode()+b'\0'+data).hexdigest()!=BLOB:
            raise ValueError('Existing variable font does not match pin')
    manifest={'source':URL,'git_blob':BLOB,'sha256':sha256(data).hexdigest(),'instances':{}}
    for weight,value in [('Regular',400),('Bold',700),('Light',300)]:
        path=dest/f'SourceHanSansSC-{weight}.ttf'
        with TTFont(source) as font:
            axes={a.axisTag:a for a in font['fvar'].axes}
            if set(axes)!={'wght'} or not axes['wght'].minValue<=value<=axes['wght'].maxValue:
                raise ValueError('Unexpected variable font axis')
            fixed=instantiateVariableFont(font,{'wght':value},inplace=True)
            fixed.save(path)
        manifest['instances'][path.name]={'weight':value,'sha256':sha256(path.read_bytes()).hexdigest()}
    (dest/'cjk'/'1902.8-font-provenance.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))

if __name__=='__main__':main(Path(sys.argv[1]))
