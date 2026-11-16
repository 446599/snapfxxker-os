#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""U1 presentation contracts and measured headless SDL capture checks.

Run from the reconstructed tree:
  U1_DISPLAY_EVIDENCE=/absolute/after-host-mock/capture-events.json \
    python3 tests/python/test_u1_display_refactor.py
The evidence is emitted by capture_host_mock.py using the supported ctl API.
No printer, motion command, desktop window or cross compiler is used.
"""
import json,os,unittest,xml.etree.ElementTree as E
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def xml(file):return E.parse(ROOT/'ui_xml'/file).getroot()
def named(root,name):return next(x for x in root.iter() if x.get('name')==name)
class PresentationContracts(unittest.TestCase):
 def test_font_scale_and_touch_floor(self):
  r=xml('globals.xml')
  self.assertEqual(named(r,'u1_title_font').get('value'),'noto_sans_24')
  self.assertEqual(named(r,'u1_body_font').get('value'),'noto_sans_18')
  self.assertGreaterEqual(int(named(r,'u1_touch_target').get('value')),44)
 def test_library_is_a_subordinate_action(self):
  r=xml('head_inventory.xml');modes=named(r,'inventory_modes')
  self.assertEqual(modes.find('bind_flag_if').get('cond'),'inventory_external')
  self.assertEqual([x.get('name') for x in modes if x.tag=='ui_button'],['inventory_view_ace','inventory_view_head'])
  self.assertIn(named(r,'inventory_library'),list(named(r,'inventory_more')))
  self.assertIn(named(r,'inventory_heads'),list(named(r,'inventory_browser')))
 def test_supply_and_candidate_are_independent(self):
  r=xml('head_inventory.xml');card=named(r,'inventory_card_${i}')
  conditions=' '.join(x.get('cond','') for x in card.findall('bind_style_if'))
  self.assertIn('inventory_selected_card',conditions)
  self.assertIn('inventory_unit_${i}_current',conditions)
  self.assertEqual(named(r,'inventory_source').get('bind_text'),'inventory_source')
 def test_load_unload_precede_quiet_tools(self):
  r=xml('material_workspace.xml')
  self.assertEqual(named(r,'workspace_load').get('variant'),'primary')
  self.assertEqual(named(r,'workspace_unload').get('variant'),'outline')
  for name in ['workspace_edit','workspace_manual']:
   self.assertEqual(named(r,name).get('variant'),'ghost')
  for name,action in [('workspace_load','load'),('workspace_unload','unload'),('workspace_manual','manual')]:
   button=named(r,name)
   self.assertEqual(button.find('event_cb').get('user_data'),action)
   self.assertIn('workspace_busy',' '.join(x.get('cond','') for x in button.findall('bind_state_if')))
 def test_calibration_controls_have_separate_dock(self):
  r=xml('path_calibration.xml');header=named(r,'overlay_header');dock=named(r,'path_calibration_controls')
  self.assertEqual([x.get('name') for x in header if x.tag=='ui_button'],['path_calibration_back'])
  self.assertEqual(dock.find('bind_flag_if').get('cond'),'not path_calibration_active')
  for name,action in [('path_calibration_stop','stop'),('path_calibration_resume','resume'),('path_calibration_cancel','cancel')]:
   button=named(r,name);self.assertIn(button,list(dock));self.assertEqual(button.get('height'),'#u1_touch_target')
   self.assertEqual(button.find('event_cb').get('user_data'),action)

class MeasuredLayout(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  evidence=os.environ.get('U1_DISPLAY_EVIDENCE')
  if not evidence:raise unittest.SkipTest('Set U1_DISPLAY_EVIDENCE to native SDL capture-events.json')
  cls.events=json.loads(Path(evidence).read_text())
 def geometry(self,root):
  return next(x['result']['widgets'] for x in self.events if x['command'][:2]==['geom',root])
 def widget(self,root,name):return next(x for x in self.geometry(root) if x['name']==name)
 def test_four_bottom_targets_remain_visible_on_each_page(self):
  for name in ['nav_btn_home','nav_btn_controls','nav_btn_filament','nav_btn_settings']:
   captures=[x['result']['widgets'][0] for x in self.events if x['command']==['geom',name]]
   self.assertGreaterEqual(len(captures),7)
   for g in captures:
    self.assertGreaterEqual(g['w'],44);self.assertGreaterEqual(g['h'],44)
    self.assertGreaterEqual(g['y'],274);self.assertLessEqual(g['y']+g['h'],320)
 def test_material_toolbar_and_main_actions_fit(self):
  widgets=self.geometry('head_inventory');get=lambda name:next(x for x in widgets if x['name']==name)
  modes=get('inventory_modes');groups=get('inventory_heads')
  self.assertEqual(modes['y'],groups['y']);self.assertGreaterEqual(modes['y'],44)
  for name in ['inventory_card_0','inventory_card_1','inventory_card_2','inventory_card_3','inventory_load','inventory_details']:
   g=get(name);self.assertGreaterEqual(g['h'],44);self.assertLessEqual(g['y']+g['h'],274)
  self.assertGreater(get('inventory_more')['y'],get('inventory_ace_actions')['y'])
 def test_detail_primary_and_secondary_are_visible(self):
  load=self.widget('material_workspace','workspace_load');unload=self.widget('material_workspace','workspace_unload')
  self.assertEqual(load['y'],unload['y'])
  for name in ['workspace_load','workspace_unload','workspace_edit','workspace_manual']:
   g=self.widget('material_workspace',name);self.assertGreaterEqual(g['h'],44);self.assertLessEqual(g['y']+g['h'],274)
  self.assertGreater(self.widget('material_workspace','workspace_edit')['y'],load['y'])
 def test_calibration_header_is_not_crowded(self):
  back=self.widget('path_calibration','path_calibration_back')
  self.assertGreaterEqual(back['h'],44);self.assertLessEqual(back['y']+back['h'],44)
  self.assertTrue(self.widget('path_calibration','path_calibration_controls')['hidden'])
 def test_no_clipped_non_scrolling_content(self):
  for event in self.events:
   if event['command'][0]!='overflow':continue
   self.assertFalse([x for x in event['result']['overflowing'] if not x['scrollable']],event['command'])
 def test_active_calibration_dock_stays_above_navigation(self):
  snapshots=[x['result']['widgets'] for x in self.events if x['command'][:2]==['geom','path_calibration']]
  self.assertGreaterEqual(len(snapshots),2)
  widgets=snapshots[-1];get=lambda name:next(x for x in widgets if x['name']==name)
  dock=get('path_calibration_controls');self.assertFalse(dock['hidden']);self.assertGreaterEqual(dock['y'],44)
  for name in ['path_calibration_stop','path_calibration_resume','path_calibration_cancel']:
   g=get(name);self.assertGreaterEqual(g['h'],44);self.assertGreaterEqual(g['y'],dock['y']);self.assertLessEqual(g['y']+g['h'],274)
 def test_tabs_navigate_to_expected_sections(self):
  states=[x['result'] for x in self.events if x['command']==['current']]
  self.assertGreaterEqual(len(states),4)
  rendered=' '.join(json.dumps(x) for x in states)
  for section in ['home','controls','filament','settings']:self.assertIn(section,rendered)
if __name__=='__main__':unittest.main(verbosity=2)
