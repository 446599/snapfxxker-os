# SPDX-License-Identifier: GPL-3.0-or-later
"""Advanced filament options, confirmed against the live backend policy."""
ID = 'ace_auto_insert_rfid'
REMAINING_ID = 'ace_remaining_estimate'
AUTO_ID = 'ace_insert_rfid_auto_enabled'
IDS = (ID, REMAINING_ID, AUTO_ID)


def policy_snapshot(klippy):
    data = klippy('objects/query', {'objects': {'ace': ['insert_policy']}})
    policy = data.get('status', {}).get('ace', {}).get('insert_policy')
    if (not isinstance(policy, dict) or not isinstance(policy.get('advanced'), dict)
            or any(type(policy['advanced'].get(key)) is not bool for key in ('rfid', 'remaining'))):
        raise ValueError('耗材高级选项暂不可用，请先更新耗材管理组件')
    units = policy.get('units')
    if not isinstance(units, list) or any(not isinstance(unit, dict) for unit in units):
        raise ValueError('ACE 插料设置状态无效')
    return policy


def settings(klippy):
    error = ''
    try:
        policy = policy_snapshot(klippy)
    except (ValueError, OSError) as exc:
        policy = {}; error = str(exc)
    advanced = policy.get('advanced', {})
    online = [unit for unit in policy.get('units', []) if unit.get('connected') is True]
    supported = any(unit.get('supported') is True for unit in online)
    rows = []
    for unit in policy.get('units', []):
        idx = unit.get('ace')
        if type(idx) is not int or not 0 <= idx < 4:
            continue
        if unit.get('connected') is not True: label = '未连接'
        elif unit.get('supported') is not True: label = '需要更新'
        elif unit.get('applied') is not False or unit.get('error'): label = '等待同步'
        else: label = '短吸已就绪'
        rows.append('ACE %d：%s' % (idx + 1, label))
    result = []
    for ident, feature, label, minimum in (
            (ID, 'rfid', 'RFID 识别（高级）', 800),
            (REMAINING_ID, 'remaining', '耗材余量估计（实验）', 1400)):
        enabled = advanced.get(feature) is True
        description = ('开启后允许已确认料路进行 RFID 识别。是否在插料后自动识别由独立开关控制。'
                       if feature == 'rfid' else
                       '默认关闭，需先开启 RFID。识别时可增加最长 1200 mm 的测量；只对已验证的料盘和通道估算，其他情况显示未知。')
        description += '\n测量短吸后的料头到 Y 汇合点前的空闲距离，不是 ACE 到喷头的总管长。'
        description += '\n' + ('  ·  '.join(rows) or error or '未连接 ACE')
        if enabled:
            description += '\n已确认最短可用长度：%d mm' % advanced.get('clearance_mm', 0)
        can_enable = supported and (feature == 'rfid' or advanced.get('rfid') is True)
        confirm = ('确认所有已连接 ACE 的每条支路：短吸后的料头到 Y 前至少有 %d mm 无障碍空间，'
                   '不会顶到其他耗材。改管路或更换 ACE 后须关闭并重新确认。开启本身不送料，下次插料或手动识别才走料。') % minimum
        result.append({'id': ident, 'group': 'advanced_materials', 'source_group': 'multiace',
            'label': label, 'source_label': label, 'description': description, 'help_url': '',
            'current': ('1' if enabled else '0') if policy else '',
            'hidden': False, 'requires_idle': True, 'policy': policy,
            'options': [
                {'value': '0', 'label': '关闭', 'source_label': 'Off', 'hidden': not bool(policy),
                 'inputs': [], 'confirm': ('关闭后插料仅吸入 100 mm（10 cm），自动和手动 RFID 及余量估计一同关闭；正常装退料与编辑仍可用。'
                                          if feature == 'rfid' else '关闭后保留 RFID 识别，不再主动测量余量。')},
                {'value': '1', 'label': '开启 · 先确认 Y 前空间', 'source_label': 'On',
                 'hidden': not can_enable, 'inputs': [
                     {'name': 'CLEARANCE_MM', 'label': '所有支路中最短的 Y 前可用长度（mm，至少 %d）' % minimum,
                      'type': 'number', 'required': True, 'default': ''}], 'confirm': confirm}]})
    if type(advanced.get('auto_rfid')) is bool:
        label = '插料后自动识别 RFID'
        result.insert(1, {'id': AUTO_ID, 'group': 'advanced_materials',
            'source_group': 'multiace', 'label': label, 'source_label': label,
            'description': '开启后只在下一次插料停稳后自动识别。关闭时仍可手动识别。'
                           '只有已确认料路的 ACE 才会自动转盘。',
            'help_url': '', 'current': '1' if advanced['auto_rfid'] else '0',
            'hidden': False, 'requires_idle': True, 'policy': policy,
            'options': [
                {'value': '0', 'label': '关闭', 'source_label': 'Off', 'hidden': False,
                 'inputs': [], 'confirm': '关闭后新插料只短吸，手动识别仍可用。'},
                {'value': '1', 'label': '开启', 'source_label': 'On',
                 'hidden': advanced.get('rfid') is not True, 'inputs': [],
                 'confirm': '从下次插料生效，不会立即转动当前料盘。'}]})
    return result


def setting(klippy):
    return settings(klippy)[0]


def update(payload, klippy):
    ident = payload.get('id')
    if ident not in IDS or payload.get('value') not in ('0', '1') or payload.get('confirm') is not True:
        raise ValueError('请阅读并确认耗材高级选项说明')
    enabled = payload['value'] == '1'
    inputs = payload.get('inputs', {})
    if ident == AUTO_ID:
        if inputs != {}:
            raise ValueError('自动识别开关不需要距离参数')
        policy = policy_snapshot(klippy)
        if type(policy['advanced'].get('auto_rfid')) is not bool:
            raise ValueError('请先更新耗材管理组件')
        if enabled and not policy['advanced']['rfid']:
            raise ValueError('请先开启 RFID 识别')
        klippy('gcode/script', {'script': 'ACE_SET_INSERT_RFID_AUTO ENABLE=%d' % enabled}, timeout=10)
        result = next(item for item in settings(klippy) if item['id'] == ident)
        applied = result['policy']['advanced'].get('auto_rfid') is enabled
        return {'ok': applied, 'state': 'applied' if applied else 'unverified', 'id': ident,
                'current': result['current'], 'setting': result, 'output': '',
                'message': '已保存，下次插料生效' if applied else '设置结果待确认，请刷新'}
    if not isinstance(inputs, dict) or set(inputs) != ({'CLEARANCE_MM'} if enabled else set()):
        raise ValueError('请填写 Y 前可用长度')
    clearance = 0
    feature = 'rfid' if ident == ID else 'remaining'
    if enabled:
        raw = inputs['CLEARANCE_MM']
        if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal() or len(raw) > 5:
            raise ValueError('Y 前可用长度必须为整数毫米')
        clearance = int(raw)
        minimum = 800 if feature == 'rfid' else 1400
        if not minimum <= clearance <= 20000:
            raise ValueError('Y 前可用长度须至少 %d mm，且不超过 20000 mm' % minimum)
    command = 'ACE_SET_AUTO_INSERT_RFID' if feature == 'rfid' else 'ACE_SET_REMAINING_ESTIMATE'
    script = '%s ENABLE=%d CONFIRM_Y=%d CLEARANCE_MM=%d' % (command, enabled, enabled, clearance)
    klippy('gcode/script', {'script': script}, timeout=30)
    result = next(item for item in settings(klippy) if item['id'] == ident)
    policy = result['policy']
    online = [u for u in policy.get('units', []) if u.get('connected') is True]
    flag = feature + '_enabled'
    if any(flag in unit for unit in online):
        ready = any(unit.get(flag) is True and unit.get('supported') is True
                    and unit.get('applied') is False and not unit.get('error')
                    for unit in online)
    else:
        ready = bool(online) and all(unit.get('supported') is True and unit.get('applied') is False
                                     and not unit.get('error') for unit in online)
    applied = policy.get('advanced', {}).get(feature) is enabled and (not enabled or ready)
    return {'ok': bool(applied), 'state': 'applied' if applied else 'unverified', 'id': ident,
            'current': result['current'], 'setting': result, 'output': '',
            'message': '耗材高级选项已保存并确认' if applied else '设置结果待确认，请刷新'}
