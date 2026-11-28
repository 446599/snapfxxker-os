import { createPlatePreview, printObjectsOnPlate } from './print-plate.js';

const printControl = '/multiace/api/print-control';
const running = state => state === 'printing' || state === 'paused';
const ended = state => ['complete','cancelled','error'].includes(state);
// Klipper parses NAME as one G-code token. Retain slicer punctuation and
// non-ASCII names while rejecting controls, whitespace, and G-code separators.
const safeName = name => typeof name === 'string' && Boolean(name) &&
  [...name].every(ch => /^[\w.:()[\]-]$/.test(ch) ||
    (ch.codePointAt(0) >= 160 && !/[\s\p{C}]/u.test(ch)));
const job = status => status.print_stats || {};
const ready = status => status.webhooks?.state === 'ready';

function exclusions(status) {
  const list = status.exclude_object?.excluded_objects;
  return new Set(Array.isArray(list) ? list : []);
}
function sameJob(before, after) {
  if (!job(before).filename || job(before).filename !== job(after).filename) return false;
  return ['print_duration','total_duration'].every(key => !Number.isFinite(job(before)[key])
    || !Number.isFinite(job(after)[key]) || job(after)[key] >= job(before)[key]);
}

export function createPrintActions(ctx, refresh, {showTune = true} = {}) {
  const {el} = ctx;
  const plate = el('div', {class:'print-plate-mount'});
  const absent = el('p', {class:'muted print-plate-note'});
  const count = el('p', {class:'print-selection-count', role:'status'});
  const apply = el('button', {type:'button', class:'primary', onclick:skipSelected}, '跳过所选模型');
  const picker = el('details', {class:'print-object-picker'}, el('summary', {}, '跳过模型'),
    el('div', {class:'print-plate-layout'}, plate, el('div', {class:'print-plate-guide'},
      el('p', {}, '在俯视预览中点击模型勾选。'), count,
      el('div', {class:'print-plate-legend'}, ...[['current','正在打印'], ['selected','已勾选'],
        ['skipped','已跳过']].map(([state,label]) => el('span', {'data-state':state}, label))),
      apply)), absent);
  const bar = el('div', {class:'row print-action-bar'});
  const root = el('div', {class:'print-actions'}, bar, picker);
  const waiting = new Set(), selected = new Set();
  const hasActiveJob = status => ready(status) && running(job(status).state);
  const canSkip = (status, target, known, excluded) => hasActiveJob(status) && safeName(target)
    && known.some(row => row.name === target && row.center) && !excluded.has(target) && !waiting.has(target);
  let latest = {}, busy = false, lastPlot, openedForJob = false;

  async function execute(allowed, command, confirmation) {
    if (busy || !allowed(latest)) return;
    const before = latest;
    busy = true;
    render();
    try {
      if (confirmation && !(await ctx.confirm(confirmation(before)))) return;
      const current = await refresh();
      if (ctx.signal.aborted) return;
      if (!sameJob(before, current) || !allowed(current))
        throw new Error('打印任务或对象状态已变化，请重新核对。');
      const result = await command(current);
      if (result?.ok === false) throw new Error(result.detail || '打印机拒绝了操作');
      if (!ctx.signal.aborted) ctx.notice(result?.queued ? '操作已排队，等待打印机确认。' : '操作已提交，正在读取打印机状态。');
    } catch (error) {
      if (!ctx.signal.aborted) ctx.notice(error.message || String(error));
    } finally {
      if (!ctx.signal.aborted) {
        await refresh().catch(() => {});
        busy = false;
        render();
      }
    }
  }

  const allowed = states => status => ready(status) && Boolean(job(status).filename) && states.includes(job(status).state);
  const post = (path, body) => ctx.api(path, {method:'POST', body, signal:ctx.signal});
  function select(name) {
    if (busy || !canSkip(latest, name, printObjectsOnPlate(latest), exclusions(latest))) return;
    if (selected.has(name)) selected.delete(name);
    else selected.add(name);
    render();
  }

  function skipSelected() {
    const targets = [...selected];
    if (!targets.length) return;
    const stillValid = status => {
      const known = printObjectsOnPlate(status);
      const excluded = exclusions(status);
      return targets.every(name => canSkip(status, name, known, excluded));
    };
    const confirm = '跳过已勾选的 ' + targets.length + ' 个模型？本次打印将停止打印这些模型，已打印部分会保留。';
    void execute(stillValid, async status => {
      const script = targets.map(name => 'EXCLUDE_OBJECT NAME=' + name).join('\n');
      const response = await post('/printer/gcode/script', {script});
      if (response?.ok !== false && sameJob(status, latest) && running(job(latest).state)) {
        for (const name of targets) {
          waiting.add(name);
          selected.delete(name);
        }
      }
      return response;
    }, () => confirm);
  }

  const controls = [
    ['暂停', allowed(['printing']), () => post(printControl,{verb:'pause'})],
    ['继续', allowed(['paused']), () => post(printControl,{verb:'resume'}),
      () => '继续当前打印任务？'],
    ['取消打印', allowed(['printing','paused']), () => post(printControl,{verb:'cancel'}),
      () => '取消当前打印任务？本次打印将结束。'],
    ['重新打印', status => ready(status) && Boolean(job(status).filename) && ended(job(status).state),
      status => post('/multiace/api/history/reprint',{filename:job(status).filename}),
      status => '重新打印「' + job(status).filename + '」？将沿用打印机里的原文件和换料设置，请先核对耗材。']
  ].map(([label, allow, command, confirmation]) => {
    const button = el('button', {type:'button', class:label === '取消打印' ? 'danger' : '',
      onclick:() => void execute(allow, command, confirmation)}, label);
    bar.append(button);
    return {button, allow, label};
  });
  const tune = showTune ? el('a', {class:'print-tune', href:'#control'}, '打印调节') : null;
  if (tune) bar.append(tune);

  function render() {
    const state = job(latest).state;
    const filePresent = Boolean(job(latest).filename);
    for (const {button, allow, label} of controls) {
      button.hidden = !filePresent || (label === '重新打印' ? !ended(state) : !running(state));
      button.disabled = busy || !allow(latest);
    }
    if (tune) tune.hidden = !running(state);
    bar.hidden = !filePresent || (!running(state) && !ended(state));
    picker.hidden = !filePresent || !running(state);
    if (picker.hidden) {
      picker.open = false;
      return;
    }

    const known = printObjectsOnPlate(latest);
    const excluded = exclusions(latest);
    const current = latest.exclude_object?.current_object;
    const rows = known.filter(row => row.center).map(row => ({
      ...row, skipped:excluded.has(row.name), pending:waiting.has(row.name),
      current:row.name === current, selected:selected.has(row.name),
      allowed:!busy && canSkip(latest, row.name, known, excluded)
    }));
    if (rows.length && !openedForJob) {
      picker.open = true;
      openedForJob = true;
    }
    const missing = known.length - rows.length;
    absent.hidden = Boolean(rows.length && !missing);
    absent.textContent = missing ? missing + ' 个模型没有俯视定位信息，无法从预览中选择。'
      : '当前任务未提供模型的俯视定位信息。';
    plate.parentElement.hidden = !rows.length;
    count.textContent = '已勾选 ' + selected.size + ' 个'
      + (waiting.size ? ' · ' + waiting.size + ' 个等待设备确认' : '');
    apply.disabled = busy || !selected.size || !hasActiveJob(latest);
    const signature = JSON.stringify(rows);
    if (signature === lastPlot) return;
    lastPlot = signature;
    const active = document.activeElement;
    const focused = plate.contains(active) ? active.dataset.object : '';
    plate.replaceChildren(...(rows.length ? [createPlatePreview(el, rows, select)] : []));
    if (focused) {
      [...plate.querySelectorAll('button')].find(node => node.dataset.object === focused)?.focus({preventScroll:true});
    }
  }

  render();
  return {root, openPicker() {
    if (picker.hidden) return;
    picker.open = true;
    picker.scrollIntoView({block:'nearest'});
  }, update(status) {
    if (!sameJob(latest, status) || (!running(job(latest).state) && running(job(status).state))) {
      selected.clear();
      waiting.clear();
      openedForJob = false;
    }
    latest = status;
    const known = printObjectsOnPlate(status), excluded = exclusions(status);
    for (const name of excluded) waiting.delete(name);
    for (const name of selected) {
      if (!canSkip(status, name, known, excluded)) selected.delete(name);
    }
    render();
  }};
}
