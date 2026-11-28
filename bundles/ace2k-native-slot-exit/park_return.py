"""Per-route verified return distance. Encoder measurement is a proposal, not motion."""
import json
import os
import tempfile
from pathlib import Path

def key(uid,slot):
    if not isinstance(uid,str) or not uid.startswith('uid:') or type(slot) is not int or not 0<=slot<4:
        raise ValueError('Invalid ACE park route')
    return '%s/%d/%d'%(uid,slot,slot)

def valid(mm):
    return type(mm) is int and 100<=mm<=5000

def encoder_delta_mm(start,end):
    if any(type(n) is not int or not -(2**31)<=n<2**31 for n in (start,end)):
        raise ValueError('Invalid native encoder counter')
    return ((end-start+2**31)%2**32-2**31)/1000.

def encoder_distance_mm(start,end):
    mm=abs(encoder_delta_mm(start,end))
    if not 100<=mm<=5000:
        raise ValueError('Measured path outside bounds')
    return mm

class ParkReturnStore:
    def __init__(self,path):
        self.path=Path(path)

    def _load(self):
        try:
            data=json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {'version':1,'routes':{}}
        if not isinstance(data,dict) or data.get('version')!=1 or not isinstance(data.get('routes'),dict):
            raise ValueError('Invalid ACE park calibration store')
        return data

    def length(self,uid,slot):
        row=self._load()['routes'].get(key(uid,slot))
        if not isinstance(row,dict) or row.get('verified') is not True or row.get('uid')!=uid or row.get('slot')!=slot or row.get('head')!=slot or not valid(row.get('retract_mm')):
            raise ValueError('No verified ACE park return for this route; calibrate this slot')
        return row['retract_mm']

    def _save(self,data):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix='.ace-park-',dir=self.path.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as f:
                json.dump(data,f,sort_keys=True,indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp,self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def record(self,uid,slot,mm):
        if not valid(mm):
            raise ValueError('Verified distance must be 100..5000 mm')
        data=self._load()
        data['routes'][key(uid,slot)]=dict(uid=uid,slot=slot,head=slot,retract_mm=mm,verified=True)
        self._save(data)

    def forget(self,uid,slot):
        data=self._load()
        if data['routes'].pop(key(uid,slot),None) is not None:
            self._save(data)
