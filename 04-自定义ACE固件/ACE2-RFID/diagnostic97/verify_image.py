"""Offline image and source provenance check; no device I/O."""
import hashlib,json,struct
from pathlib import Path
here=Path(__file__).resolve().parent; root=here.parent
base=(root/'input/ACE2_V1.1.31_20260306.bin').read_bytes()
old=(root/'diagnostic96/ACE2-96O-diagnostic.bin').read_bytes()
image=(here/'ACE2-97O-diagnostic.bin').read_bytes()
repeat=(here/'repeat.bin').read_bytes()
patch=json.loads((here/'firmware/patch.json').read_text())
sha=lambda x:hashlib.sha256(x).hexdigest()
assert image==repeat and image[-8:]==base[-8:]==bytes.fromhex('61a5635a65a5325a')
assert image[:0x200]==base[:0x200]
assert image.count(b'V1.1.97O\0')==1 and b'V1.1.96O\0' not in image
assert hashlib.md5(base).hexdigest()==patch['base_md5']
allowed=set()
for section in ('hooks','pokes'):
 for h in patch[section]:
  data=bytes.fromhex(h['replace_hex'] if section=='hooks' else h['bytes_hex'])
  off=h['file_offset'];allowed.update(range(off,off+len(data)))
  assert image[off:off+len(data)]==data
unexpected=[i for i,(a,b) in enumerate(zip(base[:-8],image)) if a!=b and i not in allowed]
assert not unexpected,unexpected
oldfw=root/'diagnostic96/firmware';newfw=here/'firmware'
changed=[]
for f in sorted(oldfw.iterdir()):
 if f.is_file() and f.name!='patch.json' and f.read_bytes()!=(newfw/f.name).read_bytes(): changed.append(f.name)
assert changed==['build_patch.py'],changed
crc=0
for byte in image:
 crc ^= byte
 for _ in range(8):crc=(crc>>1)^0x8408 if crc&1 else crc>>1
assert crc==patch['result_crc16']
# ACE OTA library load_image starts Kermit state at 0xffff; the source
# patch generator/replay uses init 0x0000. They are different checksums.
ota_crc=0xffff
for byte in image:
 ota_crc ^= byte
 for _ in range(8):ota_crc=(ota_crc>>1)^0x8408 if ota_crc&1 else ota_crc>>1
assert ota_crc==0x159d
# 97 is 96 plus six double literal bytes and the single version digit.
blocks=(0x0800bcd8,0x0800bce0,0x0800bce8,0x0800bcf0,0x0800bcf8,0x0800bd00)
allowed_delta={0x08018c26-0x08008000}
for address in blocks: allowed_delta.update(range(address-0x08008000,address-0x08008000+8))
assert len(old)==len(image)
assert all(a==b or i in allowed_delta for i,(a,b) in enumerate(zip(old,image)))
manifest={
 'status':'offline candidate; NOT FLASHED OR DEPLOYED',
 'version':'V1.1.97O','image':'ACE2-97O-diagnostic.bin','size_bytes':len(image),
 'sha256':sha(image),'md5':hashlib.md5(image).hexdigest(),'crc16_kermit_init0000_patch_replay_hex':f'{crc:04x}',
 'crc16_kermit_initffff_ota_transport_hex':f'{ota_crc:04x}',
 'base_sha256':sha(base),'base_md5':hashlib.md5(base).hexdigest(),
 'previous_96_sha256':sha(old),'repeat_sha256':sha(repeat),'repeat_identical':True,
 'tail_hex':image[-8:].hex(),'first_512_vector_bytes_identical_to_stock':True,
 'first_four_words':[f'0x{x:08x}' for x in struct.unpack_from('<4I',image)],
 'stock_region_changes_only_generated_hooks_and_pokes':True,
 'changed_source_files':changed,'decoder_source_sha256':sha((newfw/'native_tag_decoder.c').read_bytes()),
 'geometry_assumptions':{'core_mm':87,'width_mm':60,'filament_mm':1.75,'turns_per_layer':33,'safety_multiplier':0.9},
 'geometry_literals':[f'0x{x:08x}' for x in blocks],
 'tests':{'native_thumb_geometry_sweep_boundaries_and_sort':True,'patch_replay_byte_identical':True,'bambu_dual_read_and_failure':True,'rc522_staging_boundaries':True,'native_decoder_6_suites':True,'linked_thumb_350_diag_and_epilogue_abi':True,'linked_thumb_record_guard':True,'fake_sram_four_records':True},
 'limitations':['No hardware, optical, geometry, or concurrent dual-spool validation.', 'Native 10000mm floor and ctx+8 upper cap remain; at-limit readings are bounds, not exact estimates.', 'BB00 initially writes ctx+12 only once; native preload AB5E later replaces it with a sorted-array value when count>=2.', 'Existing Anycubic rawtag_cache reads four bytes beyond a 164-byte record; unchanged.', 'Shared RF scratch and firmware SPI handling are not fixed.', 'Full image rebuild required; no existing hooked region may be replaced in-place.']}
(here/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
print('PASS: image identity, patch sites, stock vectors, tail, replay metadata, deterministic build, and minimal source delta')
