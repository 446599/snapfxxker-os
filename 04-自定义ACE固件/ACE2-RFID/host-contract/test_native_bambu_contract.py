import copy
import unittest
from native_bambu_contract import candidate_from_response

HASH = 'a' * 64
UID = '01020304'
D = {'deployment_verified':True, 'device_uid_verified':True, 'app_alive':True,
     'image_sha256':HASH, 'device_uid':'ACE-1', 'rf_exclusion_verified':True,
     'register_restore_verified':True, 'stack_margin_verified':True,
     'firmware_version':'V1.1.TESTO','connection_generation':7,'session_generation':8}
R = {'_v2_frame_valid':True,'_v2_payload_valid':True,'_v2_command':68,'id':17,'code':0,
     'result':{'_index_present':True,'_code_present':True,'_field2_present':True,
               '_sku_present':True,'_type_present':True,'_sku_ascii':True,'_type_ascii':True,'_known_optional_valid':True,'_color_present':True,'_color_opaque':True,
               'index':3,'code':0,'tag':{'field2':0x0102},'sku':'SM'+UID,
               'type':'PETG Basic','color':[0,0,0]}}

def call(r=R,d=D,uid=UID,seq=17,slot=3,attr=lambda *x: True,tx=lambda *x: True,hashes={HASH:'V1.1.TESTO'},conn=7,sess=8,device='ACE-1'):
    return candidate_from_response(r,expected_seq=seq,target_slot=slot,expected_uid=uid,
        target_ace_uid=device,firmware_deployment=d,connection_generation=conn,session_generation=sess,
        attribution_check=attr,transaction_check=tx,trusted_releases=hashes)

class TestNativeContract(unittest.TestCase):
    def test_valid_black_and_no_temperature(self):
        result=call(); self.assertEqual(result['color'],[0,0,0]); self.assertEqual(result['uid'],UID)
        self.assertNotIn('temperature',result); self.assertNotIn('sku',result)
    def test_default_release_set_disabled(self):
        self.assertIsNone(call(hashes={}))
    def test_every_attestation_required(self):
        for key in ('deployment_verified','device_uid_verified','app_alive','rf_exclusion_verified',
                    'register_restore_verified','stack_margin_verified'):
            d=copy.deepcopy(D); d[key]=False; self.assertIsNone(call(d=d),key)
        self.assertIsNone(call(conn=99)); self.assertIsNone(call(sess=99)); self.assertIsNone(call(conn=None)); self.assertIsNone(call(sess=None)); self.assertIsNone(call(hashes={'b'*64:'V1.1.TESTO'}))
        for key in ('connection_generation','session_generation'):
            for value in (True, 1.0):
                d=copy.deepcopy(D);d[key]=value;self.assertIsNone(call(d=d))
    def test_corrupt_or_stale_envelope(self):
        for key,value in (('_v2_frame_valid',False),('_v2_payload_valid',False),('_v2_command',13),('id',16),('code',1)):
            r=copy.deepcopy(R); r[key]=value; self.assertIsNone(call(r=r),key)
    def test_each_wire_marker(self):
        for key in ('_index_present','_code_present','_field2_present','_sku_present','_type_present','_sku_ascii','_type_ascii','_known_optional_valid','_color_present','_color_opaque'):
            r=copy.deepcopy(R); r['result'][key]=False; self.assertIsNone(call(r=r),key)
    def test_identity_mismatch(self):
        for key,value in (('index',2),('code',1),('tag',{'field2':0x0201}),('sku','SMABCDEF12'),
                          ('type','  '),('type','BAD\x01'),('color',[1,2]),('color',[1,2,256]),('color',[True,2,3])):
            r=copy.deepcopy(R);r['result'][key]=value;self.assertIsNone(call(r=r),key)
        self.assertIsNone(call(uid='88010203')); self.assertIsNone(call(uid='deadbeef'))
    def test_unproven_attribution(self):
        self.assertIsNone(call(attr=lambda *x: False))
        self.assertIsNone(call(tx=lambda *x: False))
        self.assertIsNone(call(device='ACE-2'))
        def crash(*x): raise RuntimeError('unknown')
        self.assertIsNone(call(attr=crash))
    def test_old_60o_cannot_bypass(self):
        d=copy.deepcopy(D); d['firmware_version']='V1.1.60O'; d['deployment_verified']=False
        self.assertIsNone(call(d=d))

if __name__=='__main__': unittest.main()
