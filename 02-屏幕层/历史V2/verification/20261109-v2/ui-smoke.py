import json, subprocess, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
APP = ROOT/'helixscreen/build/bin/helix-screen'
OUT = ROOT/'renders/20261109-v2/screens'
checks=[]
for attempt in range(100):
    if subprocess.run([str(APP),'ctl','-s','/tmp/helix-u1-v2.sock','ping'],capture_output=True).returncode == 0:
        break
    time.sleep(.1)
else:
    raise RuntimeError('Preview socket did not become ready')
def ctl(*args):
    proc=subprocess.run([str(APP),'ctl','-s','/tmp/helix-u1-v2.sock',*args],capture_output=True,text=True,check=True)
    return json.loads(proc.stdout)
def check(value,message):
    checks.append({'check':message,'passed':bool(value)})
    assert value,message

def click(name):
    result=ctl('click',name);time.sleep(.12);return result

def shot(name):
    ctl('screenshot',str(OUT/(name+'.png')),'--stable')

ctl('navigate','home');time.sleep(.12);shot('01-首页')
ctl('navigate','print-select');time.sleep(.12);shot('02-文件')
ctl('navigate','controls');time.sleep(.12);ctl('scroll','controls_panel','0','-2000');shot('03-控制')
check(ctl('geom','card_calibration','0')['widgets'][0]['h']>=190,'Large-text control layout preserves a full-height calibration card')
ctl('scroll','card_calibration');shot('23-控制页校准工具')
check(ctl('geom','btn_bed_mesh','0')['widgets'][0]['h']>0,'Calibration actions remain visible after scrolling')
ctl('navigate','settings');time.sleep(.12);shot('04-设置')
click('row_local_firmware');time.sleep(.12)
check(ctl('get','workspace_page')['value']==3,'Local firmware update is also reachable directly from Settings')
shot('22-设置中的本地升级')
click('back_button');time.sleep(.12)
check(ctl('current')['panel']=='settings' and not ctl('current')['overlays'],'Firmware Back returns directly to Settings')
ctl('navigate','advanced');time.sleep(.12);shot('05-工具')
ctl('navigate','filament');time.sleep(.15)
check(ctl('current')['overlays'][-1]=='head_inventory','Normal Filament navigation opens the approved inventory')
check(ctl('get','inventory_unit_count')['value']==4,'Four ACE units visible')
shot('06-耗材总览')
click('inventory_head_3')
check(ctl('get','inventory_selected_head')['value']==3,'Head 4 uses fourth bays')
check(ctl('text','inventory_unit_name_0')['text']=='ACE 1','ACE names remain distinct from head numbers')
shot('07-喷头4料路')
click('inventory_head_0');click('inventory_card_1');click('action_button')
check(ctl('current')['overlays'][-1]=='material_workspace','Actions open the new workspace')
check('候选' in ctl('get','workspace_detail')['value'],'Viewed candidate is distinct from confirmed source')
check(ctl('text','workspace_status')['text']=='请选择需要的操作','Opening material actions clears the prior firmware prompt')
shot('08-耗材操作')
click('workspace_unload')
check('当前供料 ACE 1' in ctl('get','workspace_detail')['value'],'Unload confirmation names actual ACE 1 while viewing ACE 2')
shot('09-退料确认')
click('back_button');click('workspace_load')
check('ACE 2' in ctl('get','workspace_detail')['value'],'Load confirmation names selected ACE 2')
shot('10-装料确认')
click('back_button');click('workspace_library');time.sleep(.15)
check(ctl('get','workspace_spool_count')['value']==2,'Library loads its live provider data')
shot('11-耗材库')
click('workspace_spool_0')
check(ctl('text','spool_weight')['text']=='640','Spool remaining weight can be edited')
shot('12-线盘编辑')
ctl('set_value','spool_weight','-1');click('workspace_save_spool')
check('有效余量' in ctl('text','workspace_status')['text'],'Negative remaining weight is rejected')
click('back_button');click('workspace_dry');time.sleep(.2)
check(ctl('current')['overlays'][-1]=='ams_environment_overlay','Dryer page is reachable through a visible action')
shot('13-温湿度与烘干')
click('back_button');click('workspace_auto_dry');time.sleep(.15)
check(ctl('text','auto_value_1')['text']=='45','Automatic drying reads saved humidity threshold')
shot('14-自动烘干')
ctl('set_value','auto_value_2','50');click('workspace_auto_dry_save')
check('停止湿度需要低于启动湿度' in ctl('text','workspace_status')['text'],'Invalid humidity hysteresis is rejected')
click('back_button');click('workspace_settings');time.sleep(.15)
check(ctl('get','workspace_settings_ready')['value']==1,'Web continuation, PA, detection, cleaning and purge settings load')
shot('15-自动续料与设置')
click('back_button');click('workspace_firmware');time.sleep(.15)
check(ctl('get','workspace_file_count')['value']==1,'U1 local firmware list is available')
shot('16-U1本地升级')
click('workspace_file_0');time.sleep(.15)
check(ctl('get','workspace_prepared')['value']==1,'Firmware confirmation requires a prepared package token')
click('workspace_firmware_next')
check('本机 U1 打印机' in ctl('get','workspace_detail')['value'],'Confirmation explicitly names the local U1 target')
shot('17-升级确认')
click('workspace_firmware_start');time.sleep(.15)
check(ctl('get','workspace_page')['value']==5,'Mock-only upgrade flows into the status page')
shot('18-升级状态')
ctl('navigate','filament');time.sleep(.15);click('back_button');time.sleep(.15)
check(ctl('current')['panel']=='home','Inventory Back returns to Home without exposing the old filament landing')
check(not ctl('current')['overlays'],'Back leaves no stale overlays')
Path(__file__).with_name('ui-smoke.json').write_text(json.dumps({'mode':'isolated mock only','checks':checks},ensure_ascii=False,indent=2))
print(f'{len(checks)} checks passed, {len(list(OUT.glob("*.png")))} screens rendered')
