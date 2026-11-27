// U1 operator panels. All displayed temperatures, routes and jobs are read back
// from the printer. Browsing a tool only changes the local selection.
const apiBase = '/multiace/api';
const heaters = ['extruder', 'extruder1', 'extruder2', 'extruder3'];
const machineQuery = '/printer/objects/query?webhooks=state&print_stats=filename,state,print_duration&virtual_sdcard=progress&toolhead=position,homed_axes,extruder&heater_bed=temperature,target&'
  + heaters.map(name => name + '=temperature,target').join('&');
const jobNames = {standby:'待机', printing:'打印中', paused:'已暂停', complete:'打印完成', cancelled:'已取消', error:'打印错误'};
const numeric = value => typeof value === 'number' && Number.isFinite(value);
const deg = value => numeric(value) ? Math.round(value) + ' °C' : '—';
const readStatus = response => response?.result?.status || {};

function node(tag, cls, message) {
  const item = document.createElement(tag);
  if (cls) item.className = cls;
  if (message != null) item.textContent = message;
  return item;
}
function button(label, callback, cls = '') {
  const item = node('button', cls, label);
  item.type = 'button';
  item.addEventListener('click', callback);
  return item;
}
function card(title) {
  const box = node('section', 'card op-card');
  box.append(node('h3', '', title));
  return box;
}
function appendRow(box, ...children) {
  const row = node('div', 'row op-row');
  row.append(...children);
  box.append(row);
  return row;
}
function duration(seconds) {
  if (!numeric(seconds)) return '—';
  const minutes = Math.floor(seconds / 60);
  return minutes >= 60 ? Math.floor(minutes / 60) + ' 小时 ' + minutes % 60 + ' 分钟' : minutes + ' 分钟';
}
function labelValue(box, label, value) {
  const stat = node('div', 'op-stat');
  stat.append(node('small', 'muted', label), value);
  box.append(stat);
}
function controlPanel(ctx, overview) {
  const pane = node('div', 'u1-operator');
  const style = node('style');
  style.textContent = `
    .u1-operator{display:grid;gap:14px}.op-section{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(min(100%,290px),1fr))}
    .op-heads{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.op-heads button{min-width:0;display:grid;gap:7px;text-align:left;padding:12px 10px;align-content:start}
    .op-heads [aria-pressed=true]{outline:2px solid #b58200;outline-offset:-2px}.op-heads button strong{font-size:clamp(16px,2vw,25px);line-height:1.2}
    .op-heads small,.op-stat small{display:block}.op-heads .op-route{font-size:12px;overflow-wrap:anywhere}.op-stats{display:flex;flex-wrap:wrap;gap:10px 24px}
    .op-card h3{margin:0 0 12px}.op-card p{margin:6px 0}.op-card progress{width:100%;height:8px;accent-color:#d59c00}.op-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:10px 0 0}
    .op-card input[type=number]{width:7em;max-width:100%;padding:7px}.op-row label{display:flex;gap:6px;align-items:center}
    .op-camera-img{max-width:100%;height:auto;max-height:46vh;display:block;margin-top:8px}.op-stat{min-width:64px}
    @media(max-width:460px){.op-heads button{padding:9px 6px}.op-heads{gap:4px}.op-heads .op-head-label{font-size:12px}}
  `;
  pane.append(style);
  ctx.root.replaceChildren(pane);
  const stateText = node('p','muted','正在读取打印机状态…');
  stateText.setAttribute('role','status');
  pane.append(stateText);
  const headPanel = card('喷头');
  const heads = node('div','op-heads');
  let selected = Number.isInteger(ctx.head) && ctx.head >= 0 && ctx.head < 4 ? ctx.head : 0;
  let machine = {}, printControl = null, routes = null, cfg = {};
  let closed = false, pending = false, busy = false, poll = 0, revision = 0;
  const views = [];
  const enabled = [];
  const onUpdate = fn => views.push(fn);
  const ready = () => machine.webhooks?.state === 'ready';
  const job = () => machine.print_stats || {};
  const validJob = () => Object.prototype.hasOwnProperty.call(jobNames, job().state);

  function update() {
    if (closed) return;
    for (const fn of views) fn();
    for (const [element, allowed] of enabled) element.disabled = busy || !allowed();
  }
  async function readMachine() {
    try { machine = readStatus(await ctx.api(machineQuery, {signal:ctx.signal})); }
    catch (error) { machine = {}; throw error; }
  }
  async function refresh() {
    if (closed || busy || pending) return;
    pending = true;
    const version = revision;
    const options = {signal:ctx.signal};
    const [core, ctl, ace, config] = await Promise.allSettled([
      ctx.api(machineQuery, options), ctx.api(apiBase + '/print-control', options),
      ctx.api(apiBase + '/state', options),
      Object.keys(cfg).length || overview ? Promise.resolve(null) : ctx.api('/printer/objects/query?configfile=settings', options)
    ]);
    pending = false;
    if (closed || busy) return;
    if (revision !== version) { void refresh(); return; }
    machine = core.status === 'fulfilled' ? readStatus(core.value) : {};
    printControl = ctl.status === 'fulfilled' && typeof ctl.value?.state === 'string' ? ctl.value : null;
    routes = ace.status === 'fulfilled' && Array.isArray(ace.value?.toolheads) ? ace.value : null;
    if (config.status === 'fulfilled' && config.value) cfg = readStatus(config.value).configfile?.settings || {};
    update();
  }
  async function execute(allowed, send, confirmText) {
    if (closed || busy || !allowed()) return;
    busy = true;
    revision++;
    update();
    try {
      if (confirmText && !(await ctx.confirm(confirmText))) return;
      if (closed) return;
      await readMachine();
      if (closed) return;
      if (!allowed()) throw new Error('设备状态已变化，请核对后重试。');
      const result = await send();
      if (result?.ok === false) throw new Error(result.detail || '设备拒绝了操作');
      if (!closed) ctx.notice(result?.queued ? '命令已排队，请等待设备执行。' : '命令已提交，正在读取设备状态。');
    } catch (error) {
      if (!closed) ctx.notice(error.message || String(error));
    } finally {
      busy = false;
      if (!closed) { update(); void refresh(); }
    }
  }
  function action(label, allowed, send, confirmText, cls) {
    const b = button(label, () => execute(allowed, send, confirmText), cls);
    b.disabled = true;
    enabled.push([b, allowed]);
    return b;
  }
  function verb(name, value) {
    return ctx.api(apiBase + '/print-control', {method:'POST', body:{verb:name,...(value == null ? {} : {value})}});
  }

  onUpdate(() => {
    const state = machine.webhooks?.state;
    stateText.textContent = !state ? '无法读取打印机状态，请检查连接。'
      : state !== 'ready' ? 'Klipper 状态 · ' + state
      : '打印机已连接' + (routes ? ' · ACE 在线 ' + (routes.aces || []).filter(a => a.connected === true).length + ' 台' : ' · 暂无 ACE 数据');
  });
  heaters.forEach((name, index) => {
    const select = button('', () => {
      if (busy) return;
      selected = index;
      ctx.head = index;
      if (overview) ctx.open('control');
      else { nozzleInput.value = ''; update(); }
    });
    const title = node('span','op-head-label', '喷头 ' + (index+1));
    const temp = node('strong');
    const route = node('span','muted op-route');
    select.append(title, temp, route);
    heads.append(select);
    onUpdate(() => {
      const lane = routes?.toolheads?.find(h => h.idx === index);
      select.disabled = busy;
      select.setAttribute('aria-pressed', String(!overview && index === selected));
      temp.textContent = deg(machine[name]?.temperature);
      if (lane?.filament_at_extruder === false) route.textContent = '喷头空载';
      else if (lane?.head_source_known === true && Number.isInteger(lane.ace) && Number.isInteger(lane.slot))
        route.textContent = 'ACE ' + (lane.ace+1) + ' · 槽 ' + (lane.slot+1);
      else route.textContent = lane?.manual ? '手动供料' : lane?.feeder ? '侧边供料' : '来源待确认';
    });
  });
  headPanel.append(heads);
  pane.append(headPanel);
  const columns = node('div','op-section');
  pane.append(columns);
  const printCard = card('打印任务');
  const filename = node('strong');
  const jobText = node('p','muted');
  const progress = node('progress');
  progress.max = 100;
  progress.setAttribute('aria-label','打印进度');
  onUpdate(() => {
    const j = job();
    const p = machine.virtual_sdcard?.progress;
    filename.textContent = j.filename || (j.state === 'standby' ? '等待任务' : '—');
    progress.hidden = !numeric(p);
    if (numeric(p)) progress.value = Math.max(0,Math.min(100,p*100));
    const parts = [jobNames[j.state] || '状态未知'];
    if (numeric(p) && j.filename) parts.push(Math.round(progress.value) + '%');
    if (j.filename && numeric(j.print_duration)) parts.push('已用 ' + duration(j.print_duration));
    jobText.textContent = parts.join(' · ');
  });
  printCard.append(filename, jobText, progress);
  const may = states => () => ready() && Boolean(printControl) && states.includes(job().state);
  appendRow(printCard,
    action('暂停', may(['printing']), () => verb('pause')),
    action('继续', may(['paused']), () => verb('resume'), '继续当前打印任务？'),
    action('取消打印', may(['printing','paused']), () => verb('cancel'), '取消当前打印任务？取消后无法继续本次打印。', 'danger'));
  appendRow(printCard, button('文件与打印预检', () => ctx.open('jobs')));
  columns.append(printCard);

  const temperature = card('机内温度');
  const stats = node('div','op-stats');
  const bedValue = node('b'), chamber = node('b'), mounted = node('b');
  labelValue(stats,'热床 / 目标',bedValue); labelValue(stats,'腔体',chamber); labelValue(stats,'当前挤出机',mounted);
  temperature.append(stats);
  columns.append(temperature);
  onUpdate(() => {
    bedValue.textContent = deg(machine.heater_bed?.temperature) + ' / ' + deg(machine.heater_bed?.target);
    chamber.textContent = deg(printControl?.enclosure?.temp);
    const i = heaters.indexOf(machine.toolhead?.extruder);
    mounted.textContent = i === -1 ? '—' : '喷头 ' + (i+1);
  });

  let nozzleInput;
  if (!overview) {
    const heat = card('温度设定');
    function temperatureRow(bed) {
      const label = node('label');
      const text = node('span');
      const input = node('input');
      input.type = 'number'; input.min = '0'; input.step = '1';
      input.inputMode = 'numeric';
      if (!bed) nozzleInput = input;
      label.append(text, input, document.createTextNode(' °C'));
      const heater = () => bed ? 'heater_bed' : heaters[selected];
      const limit = () => Number(cfg[heater()]?.max_temp);
      const can = () => ready() && validJob() && numeric(machine[heater()]?.temperature) && Number.isFinite(limit()) && limit() > 0;
      const set = button('应用', () => {
        const id = heater();
        const target = input.valueAsNumber;
        if (!Number.isInteger(target) || target < 0 || target > limit()) {
          ctx.notice('请输入 0 至 ' + limit() + ' 的整数温度。'); return;
        }
        void execute(() => can() && heater() === id && target <= limit(), () =>
          ctx.api('/printer/gcode/script', {method:'POST',body:{script:'SET_HEATER_TEMPERATURE HEATER=' + id + ' TARGET=' + target}}));
      });
      enabled.push([set, can]);
      onUpdate(() => {
        text.textContent = bed ? '热床' : '喷头 ' + (selected+1);
        const target = machine[heater()]?.target;
        input.placeholder = numeric(target) ? String(Math.round(target)) : '—';
        if (Number.isFinite(limit())) input.max = String(limit());
        else input.removeAttribute('max');
        input.disabled = busy || !can();
      });
      appendRow(heat, label, set);
    }
    temperatureRow(false);
    temperatureRow(true);
    columns.append(heat);

    const movement = card('移动与喷头操作');
    const xyz = node('p','muted');
    movement.append(xyz);
    onUpdate(() => {
      const pos = machine.toolhead?.position;
      xyz.textContent = Array.isArray(pos) && pos.slice(0,3).length === 3 && pos.slice(0,3).every(numeric)
        ? ['X','Y','Z'].map((a,i) => a + ' ' + pos[i].toFixed(1)).join('   ') + ' mm'
        : '位置未读取';
    });
    appendRow(movement, button('机身控制 · 移动、取放喷头与挤出', () => ctx.open('screen')));
    columns.append(movement);
    const tuning = card('打印调节');
    for (const [name,label,statusKey,min,max] of [
      ['speed','速度','speed_factor',25,300], ['flow','流量','extrude_factor',75,125], ['fan','部件风扇','fan',0,100]
    ]) {
      const tag = node('label','',label);
      const input = node('input');
      input.type = 'number'; input.step='1'; input.min=String(min); input.max=String(max);
      tag.append(input, document.createTextNode(' %'));
      const can = () => ready() && validJob() && numeric(printControl?.[statusKey]);
      const b = button('应用', () => {
        const value = input.valueAsNumber;
        if (!Number.isInteger(value) || value < min || value > max) {ctx.notice('请输入 ' + min + ' 至 ' + max + ' 的整数百分比。'); return;}
        void execute(can, () => verb(name,value));
      });
      enabled.push([b,can]);
      onUpdate(() => {input.disabled = busy || !can(); input.placeholder = numeric(printControl?.[statusKey]) ? String(printControl[statusKey]) : '—';});
      appendRow(tuning,tag,b);
    }
    columns.append(tuning);
  }

  const camera = card('相机');
  const status = node('p','muted');
  const region = node('div');
  let stream = null, cameraTimer = 0, cameraOpen = false, cameraGeneration = 0, snapshotUrl = null;
  function stopCamera() {
    cameraOpen = false;
    cameraGeneration++;
    clearTimeout(cameraTimer);
    if (stream) { stream.onload = null; stream.onerror = null; stream.removeAttribute('src'); }
    stream = null; region.replaceChildren();
    if (snapshotUrl) { URL.revokeObjectURL(snapshotUrl); snapshotUrl = null; }
  }
  async function toggleCamera() {
    if (cameraOpen) {
      stopCamera(); status.textContent = ''; cameraToggle.textContent = '查看相机'; return;
    }
    cameraOpen = true;
    const generation = ++cameraGeneration;
    cameraToggle.textContent = '关闭画面';
    status.textContent = '正在连接相机…';
    try {
      const info = await ctx.api(apiBase + '/webcam/info');
      if (closed || generation !== cameraGeneration) return;
      if (!info?.available) throw new Error('未发现可用的摄像头。');
      if (!info.proxy_path && !info.snapshot_path) throw new Error('此摄像头使用专用播放器，请打开完整相机面板。');
      stream = node('img','op-camera-img');
      stream.alt = info.name || '打印机相机';
      const cameraParam = '?cam=' + encodeURIComponent(info.name || '');
      const unavailable = () => {
        if (generation !== cameraGeneration) return;
        stopCamera(); status.textContent = '相机画面暂不可用。'; cameraToggle.textContent = '重新连接';
      };
      async function snapshot() {
        if (generation !== cameraGeneration || !stream) return;
        try {
          const response = await ctx.response(apiBase + '/webcam/snapshot' + cameraParam, {signal:ctx.signal});
          const blob = await response.blob();
          if (generation !== cameraGeneration || !stream) return;
          if (!blob.type.startsWith('image/')) throw new Error('画面格式错误');
          const previous = snapshotUrl;
          snapshotUrl = URL.createObjectURL(blob);
          stream.src = snapshotUrl;
          if (previous) URL.revokeObjectURL(previous);
          status.textContent = (info.name || '打印机相机') + ' · 定时快照';
          cameraTimer = setTimeout(snapshot, 2000);
        } catch { unavailable(); }
      }
      region.append(stream);
      if (info.proxy_path) {
        stream.onload = () => { if (generation === cameraGeneration) status.textContent = info.name || '实时画面'; };
        stream.onerror = () => {
          stream.onload = null; stream.onerror = unavailable;
          if (info.snapshot_path) void snapshot(); else unavailable();
        };
        stream.src = apiBase + '/webcam/stream' + cameraParam;
      } else { stream.onerror = unavailable; void snapshot(); }
    } catch (error) {
      if (closed || generation !== cameraGeneration) return;
      stopCamera(); status.textContent = error.message || '相机未连接'; cameraToggle.textContent = '重新连接';
    }
  }
  const cameraToggle = button('查看相机', toggleCamera);
  appendRow(camera, cameraToggle, button('完整相机面板', () => ctx.open('printhub')));
  camera.append(status, region);
  columns.append(camera);
  ctx.onCleanup(() => { closed = true; clearInterval(poll); stopCamera(); });
  update();
  void refresh();
  poll = setInterval(() => { if (!document.hidden) void refresh(); },4000);
}

export function mountOverview(ctx) { controlPanel(ctx, true); }
export function mountControl(ctx) { controlPanel(ctx, false); }
