"""Persistent, scoped hold for the slot-4 position-unconfirmed episode.

This is not a global STOP latch. Missing or invalid evidence is fail-closed for
this route. The guard deliberately has no automatic release operation.
"""
import json, os, stat, hashlib, fcntl
from contextlib import contextmanager

ROUTES_PATH = os.path.join(os.environ.get("MULTIACE_CONFIG_DIR", "/home/lava/printer_data/config"), "persistent", "multiace-rfid-location-holds.json")

PATH='/home/lava/printer_data/controlled-exploration/slot4-position-unconfirmed-20261109.json'
PORT='/dev/serial/by-path/platform-fed00000.usb-usb-0:1.3:1.0'
UID=(808780566,17445170,942880330)
MOTION=frozenset(('feed_filament','unwind_filament','feed_or_rollback_raw',
                  'start_feed_assist','update_feeding_speed','insert_policy_start'))
READ_STOP=frozenset(('stop_feed_filament','stop_feed_assist','get_status',
                     'get_info','get_feed_info','get_key_state','discover_device'))

class RouteChanged(ValueError):
    pass

class LocationHold:
    routes_default = ROUTES_PATH
    def __init__(self,path=PATH, routes_path=None):
        self.path = path
        self.routes_path=routes_path or self.routes_default
        self.routes=[]
        self._pending=[]
        self.routes_valid=True
        self._load_routes()
        self.verified_park=False
        self.valid_marker=False
        self.active=True
        try:
            st=os.lstat(path)
            if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or stat.S_IMODE(st.st_mode)!=0o600 or st.st_size>8192:
                return
            with open(path,encoding='utf8') as f: data=json.load(f)
            self.valid_marker=(isinstance(data,dict) and data.get('reason')=='position_unconfirmed_after_seek' and
                data.get('ace_uid')==list(UID) and data.get('slot')==3 and data.get('head')==3 and data.get('port')==PORT)
            if self.valid_marker and data.get('state') == 'operator_reset_empty':
                self.active = not (data.get('operator_confirmed') is True and data.get('heads_clear') is True
                    and data.get('stopped') is True and data.get('target_input_empty') is True
                    and (data.get('all_inputs_empty') is True or data.get('target_identity_verified') is True))
            if self.valid_marker and data.get('state') == 'verified_ejected':
                self.active=not (data.get('gate_empty') is True and data.get('heads_clear') is True and data.get('stopped') is True and data.get('uid_verified') is True)
            if self.valid_marker and data.get('state') == 'verified_recovered':
                self.active=not (data.get('head_triggered') is True and data.get('head_clear') is True and data.get('gate_present') is True and data.get('stopped') is True and data.get('encoder_target_reached') is True and data.get('target_mm') == 1393)
                self.verified_park = not self.active
        except (OSError,ValueError,TypeError):
            return
    def park_verified(self):
        return LocationHold(self.path, self.routes_path).verified_park

    def _read_routes(self):
        if os.path.lexists(self.routes_path + '.reset-pending'):
            raise ValueError('operator reset transaction incomplete')
        try:
            fd=os.open(self.routes_path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
            with os.fdopen(fd,'rb') as f:
                st=os.fstat(f.fileno())
                if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or stat.S_IMODE(st.st_mode)!=0o600 or st.st_size>65536:
                    raise ValueError('invalid route file')
                raw=f.read(65537)
            if len(raw)>65536:raise ValueError('oversize route file')
            data=json.loads(raw)
            if not isinstance(data,dict) or data.get('version')!=1 or not isinstance(data.get('routes'),list):
                raise ValueError('invalid route payload')
            for r in data['routes']:
                if (not isinstance(r,dict) or not isinstance(r.get('port'),str) or not r['port']
                    or not isinstance(r.get('uid'),list) or len(r['uid'])!=3
                    or any(type(x) is not int for x in r['uid'])
                    or type(r.get('slot')) is not int or not 0<=r['slot']<4
                    or ('scope' in r and r['scope'] not in ('ace','route'))
                    or ('heads' in r and r['heads'] is not None and
                        (not isinstance(r['heads'],list) or not r['heads'] or any(type(h) is not int or not 0<=h<4 for h in r['heads'])))):
                    raise ValueError('invalid route entry')
            return data['routes'],raw
        except FileNotFoundError:
            return [],None

    def _load_routes(self):
        try:self.routes,_=self._read_routes()
        except (OSError, ValueError, TypeError):self.routes_valid=False

    def _write_routes(self,routes):
        raw=(json.dumps({'version':1,'routes':routes},sort_keys=True)+'\n').encode()
        tmp=self.routes_path+'.tmp-'+os.urandom(12).hex()
        try:
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as f:
                f.write(raw);f.flush();os.fsync(f.fileno())
            os.replace(tmp,self.routes_path)
            d=os.open(os.path.dirname(self.routes_path),os.O_RDONLY)
            try:os.fsync(d)
            finally:os.close(d)
        finally:
            try:os.unlink(tmp)
            except FileNotFoundError:pass

    @contextmanager
    def _locked(self):
        directory=os.path.dirname(self.routes_path)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        fd=os.open(self.routes_path+'.lock', os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0), 0o600)
        try:
            st=os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or stat.S_IMODE(st.st_mode)!=0o600:
                raise ValueError('route lock permissions invalid')
            fcntl.flock(fd,fcntl.LOCK_EX)
            try:yield
            finally:fcntl.flock(fd,fcntl.LOCK_UN)
        finally:os.close(fd)

    def _snapshot(self):
        if not self.routes_valid:raise ValueError('route holds invalid')
        routes,raw=self._read_routes()
        for r in list(self._pending):
            if r not in routes:routes.append(r)
        return routes,raw

    @staticmethod
    def _same_route(r, port, uid, slot):
        return r.get('port') == port and r.get('uid') == list(uid) and r.get('slot') == slot

    def hold_memory(self, port, uid, slot, heads=None, scope="route"):
        if not isinstance(port,str) or not port or not isinstance(uid,(tuple,list)) or len(uid)!=3 or any(type(x) is not int for x in uid) or type(slot) is not int or not 0<=slot<4:
            raise ValueError('route identity unavailable')
        if heads is not None and (not isinstance(heads,(list,tuple)) or not heads or any(type(h) is not int or not 0<=h<4 for h in heads)):
            raise ValueError('invalid route heads')
        if scope not in ('route','ace'):raise ValueError('invalid route scope')
        route={'port':port,'uid':list(uid),'slot':slot,'heads':sorted(set(heads)) if heads is not None else None,'scope':scope}
        if route not in self._pending:self._pending.append(route)
        if route not in self.routes:self.routes.append(route)
        return route

    def persist_route(self, route):
        port,uid,slot=route['port'],route['uid'],route['slot']
        with self._locked():
            routes,_=self._snapshot()
            same=[r for r in routes if self._same_route(r,port,uid,slot)]
            merged = None if any(r.get('heads') is None for r in same) else sorted({h for r in same for h in r['heads']})
            routes=[r for r in routes if not self._same_route(r,port,uid,slot)]
            merged_scope = 'ace' if any(r.get('scope','ace') == 'ace' for r in same) else 'route'
            routes.append({**route,'heads':merged,'scope':merged_scope})
            self._write_routes(routes)
            self._pending.remove(route)
            self.refresh_routes()

    def hold_route(self, port, uid, slot, heads=None, scope="route"):
        route=self.hold_memory(port,uid,slot,heads,scope)
        self.persist_route(route)

    def route_token(self,port,uid,slot):
        with self._locked():
            routes,raw=self._snapshot()
            if not any(self._same_route(r,port,uid,slot) for r in routes):return None
            if any(self._same_route(r,port,uid,slot) for r in self._pending):raise ValueError('route hold not durable')
            if raw is None:raise ValueError('route hold file missing')
            return hashlib.sha256(raw).hexdigest()

    def release_route(self,port,uid,slot,token):
        if token is None:return
        with self._locked():
            routes,raw=self._snapshot()
            if (raw is None or hashlib.sha256(raw).hexdigest()!=token or not any(self._same_route(r,port,uid,slot) for r in routes)
                    or any(self._same_route(r,port,uid,slot) for r in self._pending)):
                raise RouteChanged('route hold changed')
            remaining=[r for r in routes if not self._same_route(r,port,uid,slot)]
            try:self._write_routes(remaining)
            except Exception:
                try:self._write_routes(routes)
                except Exception:pass
                raise
            self.refresh_routes()

    def refresh_routes(self):
        try:self.routes,_=self._snapshot()
        except Exception:self.routes_valid=False
        return self.routes_valid

    def _route_blocked(self,head,slot,port,uid,heads=None):
        if not self.refresh_routes():return True
        target = sorted(set([head] + (heads or []))) if head is not None else heads
        for r in self.routes:
            saved = r.get('heads')
            same_ace = (port is None or uid is None or port == r['port'] or tuple(uid)==tuple(r['uid']))
            if slot is not None and r.get('scope','ace') == 'ace' and same_ace:return True
            exact = (slot is not None and slot == r['slot'] and
                     (port is None or uid is None or port == r['port'] or tuple(uid)==tuple(r['uid'])))
            if exact:return True
            if saved is None or target is None:return True
            if set(saved).intersection(target):return True
        return False

    def blocks_route(self, *, head=None, idx=None, slot=None, port=None, ace_uid=None, heads=None):
        # Head 4 may still contain this route even if another source/UID is
        # selected.  Slot 4 on the original port is independently protected.
        if self._route_blocked(head,slot,port,ace_uid,heads): return True
        if not self.active: return False
        if head == 3: return True
        if slot != 3: return False
        if idx in (None,0) or port == PORT: return True
        # Unknown/reconnected hardware cannot prove that it is a different ACE.
        return ace_uid is None or tuple(ace_uid) == UID
    def rfid_blocks_wire(self, request, port, ace_uid=None, heads=None):
        if not isinstance(request,dict):return False
        method=request.get('method');params=request.get('params') or {}
        if method in READ_STOP:return False
        if method == 'raw':
            try: command=int(params.get('cmd'))
            except (TypeError,ValueError): command=None
            return command in (8,10,11) and self._route_blocked(None,None,port,ace_uid)
        if method in ('drying','drying_raw'):
            return bool(params.get('auto_roll',False)) and self._route_blocked(None,None,port,ace_uid)
        if method not in MOTION:return False
        slot=params.get('index',params.get('slot'))
        return self._route_blocked(None,slot if type(slot) is int else None,port,ace_uid,heads)

    def blocks_wire(self, idx, request, port, ace_uid=None, heads=None):
        if not isinstance(request,dict): return False
        method=request.get('method');params=request.get('params') or {}
        if method in READ_STOP: return False
        if method == 'raw':
            
            try: command=int(params.get('cmd'))
            except (ValueError,TypeError): command=None
            # Raw motor command cannot be safely scoped to a slot without
            # decoding protobuf; block only on the original ACE endpoint.
            return command in (8,10,11) and (self._route_blocked(None,None,port,ace_uid) or self.blocks_route(idx=idx,slot=3,port=port,ace_uid=ace_uid))
        if method in ('drying','drying_raw'):
            return bool(params.get('auto_roll',False)) and (self._route_blocked(None,None,port,ace_uid) or self.blocks_route(idx=idx,slot=3,port=port,ace_uid=ace_uid))
        if method not in MOTION: return False
        slot=params.get('index',params.get('slot'))
        if type(slot) is not int:
            return self._route_blocked(None,None,port,ace_uid) or self.blocks_route(idx=idx,slot=3,port=port,ace_uid=ace_uid)
        return self.blocks_route(idx=idx,slot=slot,port=port,ace_uid=ace_uid,heads=heads)
    def message(self):
        if self.active and not self.routes:
            return 'ACE槽4/喷头4耗材位置待核，相关运动已暂停；STOP和状态查询可用'
        return '耗材路线位置待核，相关运动已暂停；请退出对应ACE料槽后重新插入'
