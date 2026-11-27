const API = '/multiace/api';
const taskLabels = {
  queued: '排队中', sampling: '检查料路', head_running: '喷头操作中',
  native: '正在退出 ACE', stopping: '正在停止', awaiting_parking: '需要检查停泊位置',
  recovery: '需要恢复', complete: '完成', cancelled: '已取消', rejected: '未执行', failed: '失败',
  head_load: '装入喷头', head_unload: '从喷头退回 ACE', inspect_parked: '需检查独立支管',
};
const actionLabels = {load: '装入喷头', unload: '退回 ACE', eject: '退出 ACE'};
const terminal = new Set(['complete', 'cancelled', 'rejected', 'failed']);
const slotStates = {empty: '空槽', ready: '有料', unknown: '状态未知', nosensor: '无检测', loading: '进料中', unloading: '退料中', feeding: '送料中', assist: '辅助送料', error: '异常'};
const validIndex = value => Number.isInteger(value) && value >= 0 && value < 4;
const key = (ace, slot) => ace + '_' + slot;
const target = (ace, slot) => 'ACE ' + (ace + 1) + ' · 槽 ' + (slot + 1);
const hex = value => /^#[0-9a-f]{6}$/i.test(value || '');
const available = unit => unit?.connected === true && !unit.unit_removed;
const nativeUnit = unit => available(unit) && unit.protocol?.toLowerCase() === 'ace2k' && unit.unit_key?.startsWith('uid:');

function node(tag, text, cls) {
  const element = document.createElement(tag);
  if (text !== undefined && text !== null) element.textContent = text;
  if (cls) element.className = cls;
  return element;
}

function button(label, handler, disabled = false, cls = '') {
  const b = node('button', label, cls);
  b.type = 'button';
  b.disabled = disabled;
  b.addEventListener('click', handler);
  return b;
}

function field(label, input) {
  const wrap = node('label', label, 'mat-field');
  wrap.append(input);
  return wrap;
}

export function mountMaterial(ctx) {
  const page = node('section', null, 'u1-material');
  const styles = node('style');
  styles.textContent = `
    .u1-material {display:grid;gap:1rem;min-width:0}
    .u1-material [hidden] {display:none!important}
    .u1-material .mat-line,.u1-material .mat-actions {display:flex;flex-wrap:wrap;align-items:center;gap:.55rem}
    .u1-material .mat-line {justify-content:space-between}
    .u1-material h2,.u1-material h3,.u1-material p {margin:0}
    .u1-material .mat-grid {display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,285px),1fr));gap:.85rem}
    .u1-material .mat-unit,.u1-material .mat-box {display:grid;align-content:start;gap:.8rem;min-width:0}
    .u1-material .mat-slots {display:grid;grid-template-columns:1fr 1fr;gap:.45rem}
    .u1-material .mat-slot {background:var(--panel,#fff);color:inherit;text-align:left;display:grid;gap:.25rem;min-width:0;padding:.65rem;border:1px solid #c8c9c5;border-radius:.65rem;overflow-wrap:anywhere}
    .u1-material .mat-slot[aria-pressed=true] {border-color:#967300;box-shadow:inset 0 0 0 1px #967300;background:#fff6cb;color:#282617}
    .u1-material .mat-slot:disabled {opacity:.62}
    .u1-material .mat-chip {display:inline-block;width:.85em;height:.85em;border:1px solid #999;border-radius:50%;margin-right:.4rem}
    .u1-material .mat-field {display:grid;gap:.2rem;min-width:0}
    .u1-material input[type=color] {width:4.4rem;height:2.5rem;padding:.15rem}
    .u1-material .mat-task {border-top:1px solid #d4d3cd;padding-top:.55rem;overflow-wrap:anywhere}
    .u1-material .mat-message {white-space:pre-line;overflow-wrap:anywhere}
  `;
  const head = node('div', null, 'mat-line');
  head.append(node('h2', '料盒与料槽'), button('MultiACE 完整面板', () => ctx.open('multiace')));
  const status = node('p', '正在读取料盒…', 'muted mat-message');
  const grid = node('div', null, 'mat-grid');
  const tools = node('div', null, 'card mat-box');
  const editor = node('div', null, 'card mat-box');
  const dryerBox = node('div', null, 'card mat-box');
  dryerBox.hidden = true;
  const tasksBox = node('div', null, 'card mat-box');
  editor.hidden = true;
  tasksBox.hidden = true;
  page.append(styles, head, status, grid, tools, editor, dryerBox, tasksBox);
  ctx.root.replaceChildren(page);

  let alive = true;
  let model = null;
  let actionBusy = false;
  let polling = false;
  let taskData = [];
  let taskError = '';
  let edit = null;
  let dryUnit = null;
  let materials = [];
  let error = '';
  let lastPaint = '';
  let lastBatch = '';
  const selected = new Map();
  const readBatch = () => { try { return sessionStorage.getItem('u1.material.batch') || ''; } catch { return ''; } };
  lastBatch = readBatch();
  const request = (path, body) => ctx.api(API + '/' + path, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  });
  const batch = (operation, body) => request('material/batch/' + operation, body);
  const notify = msg => { if (alive) ctx.notice(msg); };
  const units = () => (model?.aces || []).filter(unit => validIndex(unit.idx));
  const findUnit = idx => units().find(unit => unit.idx === idx);
  const findSlot = (unit, idx) => unit?.slots?.find(s => s.idx === idx);
  const at = entry => available(findUnit(entry.ace)) && findUnit(entry.ace).unit_key === entry.serial;
  const heading = (unit, slot) => (model?.toolheads || []).find(h =>
    h.ace === unit.idx && h.slot === slot.idx && h.head_source_known && !h.load_failed && h.filament_at_extruder === true);

  function paint(force = false) {
    if (!alive) return;
    const fingerprint = JSON.stringify([model?.mode, model?.printer_state, units().map(unit =>
      [unit.idx, unit.connected, unit.unit_removed, unit.unit_key, unit.unit_name, unit.protocol, unit.has_dryer, unit.dryer?.status, unit.temp, unit.humidity,
       unit.slots?.map(s => [s.idx,s.material,s.color,s.state,s.uid])]), model?.toolheads?.map(h =>
      [h.idx,h.ace,h.slot,h.head_source_known,h.load_failed,h.filament_at_extruder]),
      [...selected], actionBusy, error]);
    if (!force && fingerprint === lastPaint) return;
    lastPaint = fingerprint;
    status.textContent = error || (model ? ('在线料盒 ' + units().filter(available).length + ' / ' + units().length +
      (model.mode === 'multi' ? ' · Multi 模式' : ' · ' + String(model.mode || '未知') + ' 模式')) : '正在读取料盒…');
    grid.replaceChildren(...units().map(unit => {
      const card = node('section', null, 'card mat-unit');
      const title = node('div', null, 'mat-line');
      title.append(node('h3', 'ACE ' + (unit.idx + 1) + (unit.unit_name ? ' · ' + unit.unit_name : '')),
        node('small', available(unit) ? '在线' : '离线', 'muted'));
      const slots = node('div', null, 'mat-slots');
      for (const slot of unit.slots || []) {
        if (!validIndex(slot.idx)) continue;
        const id = key(unit.idx, slot.idx);
        const entry = {ace: unit.idx, slot: slot.idx, serial: unit.unit_key};
        const pick = button('', () => {
          if (selected.has(id)) selected.delete(id);
          else selected.set(id, entry);
          resetEditor();
          paint(true);
        }, actionBusy || !!error || !available(unit), 'mat-slot');
        pick.setAttribute('aria-pressed', String(selected.has(id)));
        const identity = node('strong');
        const chip = node('span', '', 'mat-chip');
        if (hex(slot.color)) chip.style.backgroundColor = slot.color;
        const source = heading(unit, slot);
        identity.append(chip, document.createTextNode(slot.material || '未标记'));
        pick.append(node('small', '槽 ' + (slot.idx + 1)), identity, node('small',
          source ? ('供料 → 喷头 ' + (source.idx + 1)) : slotStates[slot.state] || '状态未知', 'muted'));
        slots.append(pick);
      }
      card.append(title, slots);
      if (unit.has_dryer) {
        const foot = node('div',null,'mat-line');
        const readings = [Number.isFinite(unit.temp) ? unit.temp + ' °C' : '',
          Number.isFinite(unit.humidity) ? unit.humidity + '% RH' : '',
          unit.dryer?.status && unit.dryer.status !== 'stop' ? '烘干中' : ''].filter(Boolean);
        foot.append(node('small',readings.join(' · '),'muted'),
          button('烘干',() => showDryer(unit),actionBusy || !available(unit) || !!error));
        card.append(foot);
      }
      return card;
    }));
    const rows = [...selected.values()];
    const active = rows.every(s => nativeUnit(findUnit(s.ace)) && at(s));
    const busy = actionBusy || !!error;
    const operates = !busy && model?.mode === 'multi' && !['printing','paused','busy'].includes(model?.printer_state) && rows.length && active;
    const row = node('div', null, 'mat-actions');
    for (const [action, label] of Object.entries(actionLabels)) {
      row.append(button(label, () => perform(() => begin(action, rows)),
        !operates || (action === 'load' && new Set(rows.map(s => s.slot)).size !== rows.length),
        action === 'load' ? 'primary' : ''));
    }
    row.append(button('编辑耗材', () => showEditor(rows[0]), busy || rows.length !== 1),
      button('清除选择', () => { selected.clear(); resetEditor(); paint(true); }, actionBusy || !rows.length));
    tools.replaceChildren(node('strong', rows.length ? '已选 ' + rows.length + ' 槽' : '选择要操作的料槽'), row);
    if (rows.length > 1 && new Set(rows.map(s => s.slot)).size < rows.length && model?.mode === 'multi') {
      tools.append(node('small', '装入喷头时，同一喷头每次只能选择一个料槽。', 'muted'));
    }
    if (edit && (!model || !at(edit) || !selected.has(key(edit.ace,edit.slot)))) resetEditor();
    if (edit) editor.querySelectorAll('button,input').forEach(el => { el.disabled = busy; });
    if (dryUnit && (!model || !at(dryUnit))) closeDryer();
    if (dryUnit) dryerBox.querySelectorAll('button,input').forEach(el => { el.disabled = busy; });
  }

  function closeDryer() {
    dryUnit = null;
    dryerBox.hidden = true;
    dryerBox.replaceChildren();
  }

  function showDryer(unit) {
    const entry = {ace:unit.idx,serial:unit.unit_key};
    dryUnit = entry;
    const minutes = node('input');
    Object.assign(minutes,{type:'number',min:'1',max:'600',step:'1',required:true,value:'240'});
    const degrees = node('input');
    Object.assign(degrees,{type:'number',min:'35',max:'80',step:'1',required:true,value:String(unit.auto_dry?.temp || 50)});
    const row = node('div',null,'mat-actions');
    const control = async start => {
      if (start && (![minutes,degrees].every(input => Number.isInteger(input.valueAsNumber) && input.valueAsNumber >= Number(input.min) && input.valueAsNumber <= Number(input.max)))) throw Error('温度范围 35–80 °C，时长范围 1–600 分钟');
      const args = {ACE:entry.ace};
      if (start) Object.assign(args,{TEMP:degrees.valueAsNumber,DURATION:minutes.valueAsNumber});
      const action = start ? ('开始 ' + args.TEMP + ' °C 烘干，持续 ' + args.DURATION + ' 分钟') : '停止烘干';
      if (!await ctx.confirm('ACE ' + (entry.ace+1) + ' ' + action + '？') || !alive) return;
      if (!at(entry) || dryUnit !== entry) throw Error('料盒身份已改变，请重新选择');
      await request('macro',{name:start ? 'ACE_DRY' : 'ACE_STOP_DRYING',args});
      notify('已提交烘干指令，请核对料盒状态');
      closeDryer();
    };
    row.append(field('温度（°C）',degrees),field('时长（分钟）',minutes),
      button('开始烘干',() => perform(() => control(true)),false,'primary'),
      button('停止烘干',() => perform(() => control(false))),button('收起',closeDryer));
    dryerBox.replaceChildren(node('h3','ACE ' + (entry.ace+1) + ' 烘干'),row);
    dryerBox.hidden = false;
    degrees.focus();
  }

  function resetEditor() {
    edit = null;
    editor.replaceChildren();
    editor.hidden = true;
  }

  function showEditor(entry) {
    const unit = findUnit(entry.ace);
    const slot = findSlot(unit,entry.slot);
    if (!slot || !at(entry)) return;
    edit = entry;
    const mat = node('input');
    mat.value = slot.material || '';
    mat.setAttribute('list','u1-material-list');
    const list = node('datalist');
    list.id = 'u1-material-list';
    materials.forEach(item => { const o = node('option'); o.value = item; list.append(o); });
    const color = node('input');
    color.type = 'color';
    const initialColor = hex(slot.color) ? slot.color.toUpperCase() : '';
    color.value = initialColor || '#808080';
    let colorEdited = false;
    color.addEventListener('input', () => { colorEdited = true; });
    const row = node('div',null,'mat-actions');
    const save = button('保存耗材', () => perform(async () => {
      if (!at(entry) || edit !== entry) throw Error('料盒已切换，请重新选择');
      const payload = {ace:entry.ace, slot:entry.slot};
      if (mat.value.trim() !== (slot.material || '')) payload.material = mat.value.trim();
      if (colorEdited) payload.color = color.value.toUpperCase();
      if (Object.keys(payload).length === 2) { resetEditor(); return; }
      const saved = await request('slot-override/sync',payload);
      const actual = saved?.override;
      if (!saved?.ok || actual?.ace !== entry.ace || actual?.slot !== entry.slot ||
          (payload.material !== undefined && actual.material !== payload.material) ||
          (payload.color && String(actual.color).toUpperCase() !== payload.color)) throw Error('服务未确认保存，请刷新料槽');
      resetEditor();
      notify('已保存 ' + target(entry.ace,entry.slot));
    }), false, 'primary');
    row.append(field('材料',mat),list,field('颜色',color),save,button('取消',resetEditor));
    editor.hidden = false;
    editor.replaceChildren(node('h3',target(entry.ace,entry.slot)),row);
    if (slot.uid) editor.append(node('small', '标签 UID ' + slot.uid, 'muted'));
    if (unit.tag_read === true) editor.append(button('读取 RFID', () => perform(async () => {
      if (!await ctx.confirm('读取 ' + target(entry.ace,entry.slot) + ' 的 RFID？该料槽可能会短距离转动。') || !alive) return;
      if (!at(entry)) throw Error('料盒身份已改变');
      await request('macro', {name:'ACE_TAG_READ', args:{ACE:entry.ace,SLOT:entry.slot}});
      notify('已提交 RFID 读取，请以设备返回的标签为准');
      resetEditor();
    })));
    mat.focus();
  }

  async function begin(action, rows, continuingBatch = '') {
    const preview = continuingBatch ? await batch('parking_preview',{batch:continuingBatch})
      : await batch('preview',{action,slots:rows});
    if (!alive) return;
    if (!Array.isArray(preview.slots)) throw Error('未收到设备的料槽预检结果');
    const lines = preview.slots.map(s => target(s.ace,s.slot) + '：' + (s.detail || taskLabels[s.state] || s.state));
    if (!preview.ticket) throw Error(lines.join('\n') || '当前没有可执行的料槽');
    const physical = preview.needs_parking_confirmation === true;
    const prompt = [actionLabels[action],...lines,
      physical ? '请到机器旁逐一检查标为“需检查独立支管”的耗材。确认尖端都已离开公共料管，位于各自的独立支管后，再确认执行。' : '确认执行上述可用料槽？'];
    if (!await ctx.confirm(prompt.join('\n')) || !alive) return;
    const accepted = await batch('start',{ticket:preview.ticket,confirm_parked:physical});
    if (!accepted.batch || !Array.isArray(accepted.tasks) || !accepted.tasks.length) throw Error('设备未确认受理，请检查任务记录');
    lastBatch = accepted.batch;
    try { sessionStorage.setItem('u1.material.batch',lastBatch); } catch {}
    selected.clear();
    resetEditor();
    notify('已受理 ' + accepted.tasks.length + ' 个料槽，请查看各槽任务状态');
  }

  function paintTasks() {
    if (!alive) return;
    const rows = [...taskData.filter(t => !terminal.has(t.state)),...taskData.filter(t => terminal.has(t.state)).slice(-16).reverse()];
    tasksBox.hidden = !rows.length && !taskError;
    tasksBox.replaceChildren(node('h3','料槽任务'));
    if (taskError) tasksBox.append(node('p',taskError,'mat-message'));
    const parkingBatches = new Set();
    for (const task of rows) {
      if (!validIndex(task.ace) || !validIndex(task.slot)) continue;
      const row = node('div',null,'mat-task mat-line');
      row.append(node('span',target(task.ace,task.slot) + ' · ' + (actionLabels[task.action] || task.action) + ' · ' +
        (taskLabels[task.state] || task.state)));
      if (task.detail) row.append(node('small',task.detail, 'muted'));
      if (!terminal.has(task.state) && task.cancel_mode && task.cancel_mode !== 'none' && task.state !== 'stopping') {
        row.append(button('停止此槽',() => perform(async () => {
          if (!await ctx.confirm('停止 ' + target(task.ace,task.slot) + ' 的此项任务？') || !alive) return;
          const result = await batch('stop',{task:task.id});
          if (!result.accepted) throw Error('停止未受理，请核对当前任务状态');
          notify(result.state === 'after_head' ? '将在当前喷头操作结束时停止' : '已受理停止请求');
        }), actionBusy || !!taskError));
      }
      if (task.state === 'awaiting_parking') parkingBatches.add(task.batch);
      tasksBox.append(row);
    }
    for (const id of parkingBatches) {
      const bays = rows.filter(t => t.batch === id && t.state === 'awaiting_parking').map(t => target(t.ace,t.slot));
      tasksBox.append(button('检查停泊 · ' + bays.join('、'), () => perform(() => begin('eject', [], id)),actionBusy || !!taskError));
    }
  }

  async function readTasks() {
    try {
      const data = await ctx.api('/printer/objects/query?ace=material_batch_tasks');
      const tasks = data?.result?.status?.ace?.material_batch_tasks;
      if (Array.isArray(tasks)) return tasks;
    } catch (err) { if (!lastBatch) throw err; }
    if (!lastBatch) return [];
    const data = await batch('status',{batch:lastBatch});
    if (!Array.isArray(data.tasks)) throw Error('任务状态未知');
    return data.tasks;
  }

  async function refresh() {
    if (polling || !alive) return;
    polling = true;
    try {
      const [state, tasks] = await Promise.allSettled([request('state'),readTasks()]);
      if (!alive) return;
      if (state.status === 'fulfilled' && Array.isArray(state.value?.aces) && !state.value?.mock) {
        model = state.value;
        error = '';
        for (const [id,entry] of selected) if (!at(entry) || !findSlot(findUnit(entry.ace),entry.slot)) selected.delete(id);
      } else {
        model = null;
        error = state.status === 'rejected' ? '耗材状态读取失败：' + state.reason.message :
          '未取得打印机的实时耗材状态';
        selected.clear();
      }
      const changed = tasks.status === 'fulfilled' ? JSON.stringify(tasks.value) !== JSON.stringify(taskData) || taskError : true;
      if (tasks.status === 'fulfilled') { taskData = tasks.value; taskError = ''; }
      else { taskError = '任务状态读取失败：' + tasks.reason.message; }
      paint();
      if (changed) paintTasks();
    } finally { polling = false; }
  }

  async function perform(run) {
    if (actionBusy || !alive) return;
    actionBusy = true;
    paint(true);
    paintTasks();
    try { await run(); }
    catch (err) { notify(err.message || String(err)); }
    finally {
      actionBusy = false;
      if (alive) { paint(true); paintTasks(); await refresh(); }
    }
  }

  request('materials').then(data => { if (alive && Array.isArray(data.materials)) materials = data.materials; }).catch(() => {});
  paint();
  refresh();
  const timer = setInterval(() => { if (!document.hidden) refresh(); }, 2500);
  ctx.onCleanup(() => { alive = false; clearInterval(timer); });
}
