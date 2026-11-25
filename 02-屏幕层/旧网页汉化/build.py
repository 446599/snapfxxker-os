"""Generate a reversible display-only localization from this printer's original UI."""
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parent
original = (ROOT / 'original/index.html').read_text()
settings = json.loads((ROOT / 'original/settings.json').read_text())
items = {item['id']: item for group in settings.values() for item in group['items']}
translations = {}


def add(english, chinese):
    if isinstance(english, str) and english:
        assert english not in translations or translations[english] == chinese, english
        translations[english] = chinese


basic = {
    'Firmware Config':'固件设置', 'Status':'状态', 'Quick Actions':'快捷操作',
    'Quick Links':'快捷入口', 'Settings':'设置', 'Actions':'维护操作',
    'Firmware Upgrade':'固件升级', 'Refresh':'刷新', '↻ Refresh':'↻ 刷新',
    'Loading...':'加载中…', 'Download & Upgrade':'下载并升级', 'Upload & Upgrade':'上传并升级',
    'Cancel':'取消', 'Confirm':'确认', 'Download File':'下载文件', 'Close':'关闭',
    'OR':'或', 'Run':'执行', 'View docs':'查看文档', 'View documentation':'查看文档',
    'Enter firmware URL':'输入固件下载地址', 'Click to select or drag & drop file':'点击选择文件，或将文件拖到此处',
    'Service unavailable. Ensure':'服务不可用。请确认已开启', 'Advanced Mode':'高级模式',
    'is enabled, then restart the printer.':'，然后重启打印机。', 'Service unavailable.':'服务不可用。',
    'Refresh to reload':'刷新并重新加载', 'Authentication required. Please':'需要登录验证。请先',
    'log in to Fluidd':'登录 Fluidd', 'first, then return to this page.':'，然后返回本页。',
    'Hidden options are visible.':'当前显示隐藏选项。', 'Authentication required':'需要登录验证',
    'Service unavailable':'服务不可用', 'Invalid format':'格式不正确', 'N/A':'暂无',
    'Web':'网页界面', 'Camera':'摄像头', 'Remote Access':'远程访问',
    'Snapmaker Components':'快造组件', 'Print Preferences':'打印偏好', 'Tweaks':'性能调校',
    'Troubleshooting':'故障排查', 'Monitoring & Debugging':'监控与调试', 'Recovery':'恢复',
    'Services':'服务', 'System':'系统', 'Enabled':'启用', 'Disabled':'关闭', 'None':'不使用',
    'External':'外部读卡器', 'Default':'默认', 'Normal':'正常',
    'OpenRFID (force generic vendor)':'OpenRFID（强制通用品牌）',
    'Enabled (Force Generic Vendor)':'启用（强制通用品牌）',
    'Stable (recommended)':'稳定版（推荐）', 'Testing (pre-releases)':'测试版（预发布）',
    'Develop (bleeding edge)':'开发版（持续更新）', 'Balanced':'均衡', 'Aggressive':'激进',
    'Bypass MCU (Disconnected)':'绕过主控板（断开连接）', 'Bypass Thermistor':'绕过热敏电阻故障',
    'Force Full Mesh':'强制全床调平', 'Force Adaptive Mesh':'强制自适应调平',
    'Panda Breath (Manual mode)':'Panda Breath（手动模式）', 'DragonBreath (Recommended)':'DragonBreath（推荐）',
    'Spoolman URL':'Spoolman 服务地址', 'Panda Breath IP Address':'Panda Breath 的 IP 地址',
    'DragonBreath Device IP Address':'DragonBreath 设备的 IP 地址',
    'Filament Manager':'耗材管理', 'Web Interface':'打印机控制界面',
    'System Information':'系统信息', 'Base':'基础固件', 'Version':'构建版本', 'Profile':'固件类型',
    'Active Firmware':'当前固件槽位', 'Device Name':'设备名称', 'WLAN Network':'无线网络',
    'Ethernet Network':'有线网络', 'Signal':'信号强度', 'eMMC & Performance':'存储与性能',
    'Uptime':'运行时间', 'Load Avg':'平均负载', 'CPU Freq':'CPU 频率', 'CPU Temp':'CPU 温度',
    'eMMC Pre-EOL':'eMMC 寿命预警', 'eMMC Life Time':'eMMC 已用寿命',
    'Confirm Download & Upgrade':'确认下载并升级', 'Download and upgrade from URL? System may reboot.':'确定从此地址下载并升级固件吗？系统可能会重启。',
    'Confirm Upgrade':'确认升级', 'Upload and upgrade? System may reboot.':'确定上传并升级固件吗？系统可能会重启。',
    'Processing...':'处理中…', 'Downloading and upgrading...':'正在下载并升级…', 'Uploading...':'上传中…',
    'Upgrade':'升级', 'Installing firmware...':'正在安装固件…', 'Upgrading...':'升级中…',
    'Success - System will reboot':'操作成功，系统即将重启', 'Upgrade failed':'升级失败',
    'Completed':'已完成', 'Upload failed':'上传失败', 'Executing...':'执行中…',
    'Completed successfully':'操作成功', 'Failed':'操作失败', 'Updating Setting':'更新设置',
    'Applying changes...':'正在应用更改…',
    '⚠️ Warning: Changing settings will restart services and may interrupt printing.':'⚠️ 注意：更改设置会重启相关服务，可能中断打印。',
    'Type "I UNDERSTAND" to confirm you have read the documentation and accept the warranty and longevity risks':'请输入英文 I UNDERSTAND，确认已阅读文档并接受保修及使用寿命方面的风险',
    'Type "I UNDERSTAND" to confirm you have read the documentation and accept the warranty, safety, and longevity risks':'请输入英文 I UNDERSTAND，确认已阅读文档并接受保修、安全及使用寿命方面的风险',
}
for english, chinese in basic.items(): add(english, chinese)

labels = {
    'web-frontend':'网页控制界面', 'firmware-config':'固件设置', 'remote-screen':'远程触屏',
    'camera-internal':'内置摄像头', 'camera-internal-resolution':'内置摄像头画面规格',
    'camera-usb':'USB 摄像头', 'camera-rtsp':'RTSP 视频流（仅 Paxx12 驱动）',
    'ssh':'SSH 访问', 'authorization':'网页登录验证（实验性）', 'vpn':'VPN 远程访问（实验性）',
    'cloud':'云端远程访问（实验性）', 'rfid':'耗材标签识别', 'firmware-upgrade-channel':'固件升级',
    'afc':'AFC Lite（通过 Fluidd/Mainsail）', 'spoolman':'Spoolman 耗材管理',
    'chamber_heater':'PandaBreath / DragonBreath 腔体加热器', 'filament_feeders':'送料器配置',
    'gui':'触摸屏界面（实验性）', 'bed_leveling_force':'每次打印强制热床调平',
    'object_processing':'自适应调平的模型边界处理', 'timelapse_force':'强制延时摄影',
    'max_speed':'运动与换头提速', 'tmc_autotune':'TMC 驱动自动调优',
    'tmc_reduce_current':'降低 TMC 电机电流', 'klipper-metrics':'Klipper 监控指标导出',
}
descriptions = {
    'web-frontend':'选择 Fluidd 或 Mainsail 作为网页控制界面。',
    'firmware-config':'开启或关闭此固件设置页面。', 'remote-screen':'在浏览器中查看并操作打印机触摸屏。',
    'camera-internal':'选择内置 MIPI 摄像头使用的驱动。',
    'camera-internal-resolution':'内置 MIPI 摄像头的分辨率和帧率。分辨率固定为 1080p，只能降低帧率。',
    'camera-usb':'启用外接 USB 摄像头的视频流。', 'camera-rtsp':'输出内置摄像头的 RTSP 视频流，仅适用于 Paxx12 驱动。',
    'ssh':'通过 SSH 远程访问打印机命令行。', 'authorization':'通过 Moonraker 登录验证，为 Fluidd 和固件设置页面启用密码保护。',
    'vpn':'将打印机连接到 VPN，以便从外部网络安全访问。', 'cloud':'启用第三方云服务，从局域网外访问打印机。',
    'rfid':'选择耗材标签的识别方式。', 'firmware-upgrade-channel':'开启或关闭 Paxx12 固件升级功能，需要使用快造云服务。',
    'afc':'使用 CHANGE_TOOL 和 LANE_UNLOAD 宏管理多路耗材。',
    'spoolman':'通过 Spoolman 管理料卷信息并记录耗材用量。',
    'chamber_heater':'集成 BIQU Panda Breath 腔体加热与空气过滤设备，也支持同一控制板上的 DragonBreath 开源固件。',
    'filament_feeders':'选择送料器配置。BIQU PopStation mini 等送料路径较长的设备，需要比原装 950 mm 更长的耗材预装长度。',
    'gui':'选择打印机内置触摸屏上显示的界面。', 'bed_leveling_force':'每次打印前强制执行热床网格调平。',
    'object_processing':'由 Moonraker 提取模型边界以支持自适应调平。优先使用切片软件中的“排除对象”功能。',
    'timelapse_force':'忽略快造应用中的开关设置，强制开启延时摄影。切片软件仍需调用 TIMELAPSE_START、TIMELAPSE_TAKE_FRAME 和 TIMELAPSE_STOP。',
    'max_speed':'实验功能：提高 XY 运动限制并加快换头，会增加磨损，可能造成 Z 轴丢步等打印问题。需要先完成校准，建议从“均衡”档开始。',
    'tmc_autotune':'优化 TMC2240 驱动参数，降低噪声并改善性能。',
    'tmc_reduce_current':'将 X/Y 电机电流从 1.2A 降至 1.0A，以降低噪声和发热。',
    'klipper-metrics':'导出 Klipper 指标，供 Prometheus 采集。',
}
for key, item in items.items():
    if key.startswith('toolhead'):
        n = int(key[-1]); labels[key] = f'{n} 号工具头'
        descriptions[key] = f'临时绕过 {n} 号工具头的热敏电阻、加热或主控板故障。'
    add(item['label'], labels[key]); add(item.get('description'), descriptions.get(key, ''))


def confirm(key, option, chinese):
    add(items[key]['options'][option]['confirm'], chinese)


confirm('firmware-config', 'false', '关闭后将无法访问此页面。重新启用需要手动编辑 extended2.cfg。')
confirm('authorization', 'enabled', '确定启用登录验证吗？系统会创建一个使用随机密码的新管理员账户，之后访问 Fluidd 和固件设置页面需要登录。注意：此功能为实验功能，仅适用于 Fluidd，Mainsail 暂不支持。')
confirm('authorization', 'disabled', '确定关闭登录验证吗？关闭后，局域网内的任何人都可以访问打印机。')
for option in ['openrfid', 'openrfid-generic']:
    confirm('rfid', option, '请先阅读相关功能文档。拓竹（Bambu Lab）和创想三维（Creality）标签默认未启用，需要另行配置。')
for option in items['firmware-upgrade-channel']['options']:
    confirm('firmware-upgrade-channel', option, '更改固件升级通道需要重启打印机。现在重启吗？')
confirm('afc', 'enabled', '确定启用 AFC Lite 吗？它通过 CHANGE_TOOL 和 LANE_UNLOAD 宏提供多路耗材管理，详情请查看文档。')
confirm('spoolman', 'enabled', '确定启用 Spoolman 集成吗？请在下方填写 Spoolman 服务地址。\n\nSpoolman 会记录耗材用量，并允许通过网页管理料卷信息。')
confirm('spoolman', 'force_generic', '确定启用 Spoolman，并将品牌统一显示为 Generic 吗？请在下方填写 Spoolman 服务地址。\n\n非快造耗材在 U1 上显示为 Generic，Spoolman 中的料卷信息及用量记录保持不变。')
confirm('filament_feeders', 'popstation_mini', '确定将送料器设为 BIQU PopStation mini 吗？这会把两侧送料器的预装长度均改为 2000 mm。仅在送料器装于 PopStation mini 时使用；原装配置可能因此把耗材用力推入工具头。')
confirm('gui', 'snapmaker', '确定切回快造原厂触屏界面吗？\n\n已下载的 HelixScreen 文件会被移除，以后再次切换时需要重新下载。\n\n打印机将重启以应用更改。')
confirm('gui', 'helixscreen', '确定将快造原厂触屏界面替换为 HelixScreen 吗？\n\nHelixScreen 是第三方 Klipper 触屏界面，选择后按需下载（约 46 MB）。它自行管理 Wi-Fi，但与原厂界面共用保存的网络文件，已配置的网络会自动沿用。\n\n打印机将重启以应用更改。')
confirm('bed_leveling_force', 'force', '确定每次打印前都强制执行全床网格调平吗？')
confirm('bed_leveling_force', 'adaptive', '确定每次打印前都强制执行自适应网格调平吗？当 exclude_object 未启用时，会退回全床调平。请在切片软件中开启“排除对象”，以便 Klipper 获得模型边界；否则始终执行全床调平。')
confirm('object_processing', 'enabled', '确定启用 Moonraker 模型边界处理吗？优先选择在切片软件中开启“排除对象”。此功能可能导致大于 100 MB 的 G-code 处理缓慢、上传延迟，并让 Snapmaker Orca 长时间停在 100%。仅在切片软件不支持“排除对象”时启用。')
confirm('timelapse_force', 'enabled', '确定允许 TIMELAPSE_START 忽略快造的延时摄影开关设置吗？切片软件仍需调用 TIMELAPSE_START、TIMELAPSE_TAKE_FRAME 和 TIMELAPSE_STOP。')
confirm('tmc_autotune', 'enabled', '确定启用 TMC 自动调优吗？它会优化 TMC2240 驱动参数，以降低噪声并改善性能。首次使用时请留意电机温度、打印质量和丢步情况。')
confirm('tmc_reduce_current', 'enabled', '确定降低 TMC 电流吗？这会将 X/Y 电机电流从 1.2A 降至 1.0A，以降低噪声和发热。启用后请留意电机温度和打印质量。')
confirm('max_speed', 'balanced', '警告：实验功能。将 XY 速度上限提高到 600 mm/s（原厂为 500），换头速度提高到 550 mm/s，抓取工具头的速度提高到 30 mm/s。\n这超过打印机的额定运动限制，会增加磨损，可能造成 Z 轴丢步、错层和振纹。\n仅在已完成全部校准、原厂速度下换头可靠时启用。如果打印质量下降，请将其关闭。')
confirm('max_speed', 'aggressive', '警告：实验功能，最高档位。将 XY 速度上限提高到 750 mm/s（原厂为 500），换头速度提高到 700 mm/s，抓取工具头的速度提高到 50 mm/s。\n这远超打印机的额定运动限制，会增加磨损，可能造成 Z 轴丢步、错层和振纹。\n仅在已完成全部校准、原厂速度下换头可靠时启用。如果打印质量下降，请降低档位。')
for n in range(1, 5):
    confirm(f'toolhead{n}', 'bypass_thermistor', f'确定绕过 {n} 号工具头故障吗？这会关闭该工具头正常的温度故障处理，以便其他工具头继续使用。启用期间禁止加热或使用故障工具头打印。')
    confirm(f'toolhead{n}', 'bypass_mcu', f'确定绕过 {n} 号工具头主控板吗？仅适用于该主控板已损坏或线缆断开的情况。这会完全停用该工具头的步进电机和加热器，使其无法移动、挤出或加热，但可让打印机启动并使用其他工具头。')

panda_intro = '警告：Panda Breath 会显著提高腔体的持续温度，使电子器件、电机等部件超出额定工作条件并加速老化。由较高热应力造成的损坏很可能不在保修范围内。使用前必须为主板增加散热，详情请阅读文档。\n\n'
panda_steps = '\n\n1. 为主板增加主动散热（详见文档）。\n2. 确认 Panda Breath 使用 v1.0.3 固件。\n3. 在路由器上为打印机设置固定 DHCP 租约；其 IP 会写入 Panda Breath，重启后不能改变。\n4. 为 Panda Breath 同样设置固定 DHCP 租约，Klipper 在每次打印时会通过 IP 连接它。\n5. 将 Panda Breath 断电重启，并至少等待 5 秒。\n6. 在下方填写 Panda Breath 的 IP 地址。\n\n'
confirm('chamber_heater', 'auto', panda_intro + '启用 Panda Breath 自动模式前，请完成以下全部步骤：' + panda_steps + '自动模式先由 Klipper 加热腔体，再交给 Panda 原生自动模式保温和降温。\n\n（如果设备已刷入 DragonBreath 开源固件，请改选下方的 DragonBreath。）\n')
confirm('chamber_heater', 'manual', panda_intro + '启用 Panda Breath 手动模式前，请完成以下全部步骤：' + panda_steps + '手动模式是旧版兼容方案。如果打印期间设备或网络连接中断，其安全性可能较低。\n')
confirm('chamber_heater', 'dragonbreath', '警告：腔体加热器会提高腔体持续温度，使电子器件和电机超出额定条件并加速老化，相关损坏很可能不在保修范围内。必须增加主板散热（详见文档）。\n\nDragonBreath 是刷入同一 Panda Breath 控制板的社区开源固件，用于市电供电的加热器。不提供保修；故障可能造成过热、火灾或损坏。风险由使用者承担，禁止无人看管时运行。\n\n启用前：\n1. 为主板增加主动散热（详见文档）。\n2. 将 DragonBreath 刷入 Panda Breath 控制板并配置 Wi-Fi（详见 https://github.com/plastikman/DragonBreath）。\n3. 为 DragonBreath 设置固定 DHCP 租约，Klipper 通过 IP 连接。\n4. 在下方填写其 IP 地址。\n\n（如果 Panda Breath 仍使用原厂固件，请选择上方 Panda 自动模式。）\n\n高级选项：若需使用设备控制令牌（NVS ctl_token），启用后在 DragonBreath 配置中增加 token: 行，详见文档。\n')

action_translations = {
    'show-status':('查看控制板固件版本', '显示全部已连接控制板（MCU）的固件版本。', None),
    'collect-logs':('收集系统日志', '打包并下载系统日志，用于故障排查。', '收集系统日志可能需要几分钟。日志包含序列号、Wi-Fi 名称等敏感信息，请勿公开分享。是否继续？'),
    'tailscale-status':('Tailscale 状态', '显示 Tailscale 当前连接状态和 IP 地址。', None),
    'reset-extended-to-defaults':('恢复扩展功能默认设置', '清空全部扩展设置并重启，恢复到初始状态。', '确定将全部扩展设置恢复默认并重启吗？'),
    'reset-to-backup-firmware':('重置扩展设置并切换备用固件', '清空全部扩展设置，切换到备用固件槽位并重启。', '确定恢复全部扩展默认设置、切换到备用固件并重启吗？'),
    'switch-to-backup-firmware':('切换到备用固件', '下次重启时从备用固件槽位启动。', '确定切换到备用固件并重启吗？'),
    'restart-klipper':('重启 Klipper', '重启 Klipper 固件服务。', None),
    'restart-moonraker':('重启 Moonraker', '重启 Moonraker 接口服务。', None),
    'reboot-system':('重启打印机', '重启打印机系统。', None),
}
for group in json.loads((ROOT / 'original/actions.json').read_text()).values():
    for item in group['items']:
        label, description, question = action_translations[item['id']]
        add(item['label'], label); add(item.get('description'), description)
        if question: add(item['confirm'], question)

identity = {'Fluidd', 'Mainsail', 'Paxx12', 'Snapmaker', 'OpenRFID', 'HelixScreen', 'Tailscale', 'OctoEverywhere', 'BIQU PopStation mini', 'Panda Breath', 'I UNDERSTAND', '192.168.1.100', 'http://192.168.1.100:7912'}
def strings(value, key=''):
    if isinstance(value, dict):
        for k, v in value.items(): yield from strings(v, k)
    elif isinstance(value, list):
        for v in value: yield from strings(v, key)
    elif isinstance(value, str) and key in {'label','description','confirm','placeholder','title'} and value:
        yield value

untranslated = set()
for file in (ROOT / 'original').glob('*.json'):
    for text in strings(json.loads(file.read_text())):
        if text not in translations and text not in identity and not re.match(r'^1920x1080 @ \d+fps$', text): untranslated.add(text)
assert not untranslated, sorted(untranslated)
assert all(translations.values())
script = (ROOT / 'localize.js').read_text().replace('__TRANSLATIONS__', json.dumps(translations, ensure_ascii=False).replace('</', '<\\/'))
style = '''h1{flex-wrap:wrap;gap:10px}body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
[data-ui-language-toggle]{font-size:12px}.row{gap:16px}.row-label-wrap{min-width:0}.row select{min-width:240px;max-width:280px}
.modal-message{white-space:pre-wrap;overflow-wrap:anywhere}
@media(max-width:600px){.row{flex-wrap:wrap;gap:10px}.row-label-wrap{flex:1 1 100%}.row select{width:100%;min-width:0;max-width:100%;flex:1 1 100%}.input-row{flex-wrap:wrap}.input{min-width:0;width:100%}.links-grid{grid-template-columns:minmax(0,1fr)}}'''
addon = '<!-- BEGIN PAXX12 ZH-CN DISPLAY LAYER -->\n<style>' + style + '</style>\n<script id="paxx12-ui-zh">' + script + '</script>\n<!-- END PAXX12 ZH-CN DISPLAY LAYER -->\n'
result = original.replace('</body>', addon + '</body>')
assert result.replace(addon, '') == original
(ROOT / 'dist/index.html').write_text(result)
(ROOT / 'dist/localize.compiled.js').write_text(script)
(ROOT / 'dist/translations.json').write_text(json.dumps(translations, ensure_ascii=False, indent=2)+'\n')
manifest = {'base':'2.0.0-paxx12-23', 'original_sha256':hashlib.sha256(original.encode()).hexdigest(), 'localized_sha256':hashlib.sha256(result.encode()).hexdigest(), 'translation_count':len(translations), 'original_logic_unchanged':True, 'target':'/usr/local/share/firmware-config/html/index.html'}
(ROOT / 'dist/manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
print(json.dumps(manifest, ensure_ascii=False, indent=2))
