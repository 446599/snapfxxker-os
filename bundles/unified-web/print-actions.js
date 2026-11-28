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

function objects(status) {
  const list = status.exclude_object?.objects;
  if (!Array.isArray(list)) return [];
  return [...new Set(list.map(item => item?.name).filter(name => typeof name === 'string' && name))];
}
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
  const operands = el('div', {class:'print-object-list'});
  const pickerTitle = el('summary', {}, '跳过模型');
  const picker = el('details', {class:'print-object-picker'}, pickerTitle, operands);
  const bar = el('div', {class:'row print-action-bar'});
  const root = el('div', {class:'print-actions'}, bar, picker);
  const waiting = new Set();
  const hasActiveJob = status => ready(status) && running(job(status).state);
  const canSkip = (status, target) => hasActiveJob(status) && safeName(target)
    && objects(status).includes(target) && !exclusions(status).has(target) && !waiting.has(target);
  let latest = {}, busy = false, lastList;

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

    const names = objects(latest);
    const excluded = exclusions(latest);
    const current = latest.exclude_object?.current_object;
    const rows = names.map(name => ({
      name, skipped:excluded.has(name), pending:waiting.has(name),
      current:name === current, allowed:!busy && canSkip(latest, name)
    }));
    const signature = JSON.stringify(rows);
    if (signature === lastList) return;
    lastList = signature;
    operands.replaceChildren();
    if (!rows.length) {
      operands.append(el('p', {class:'muted'}, '当前任务未提供可跳过的对象。'));
      return;
    }

    for (const row of rows) {
      const {name, skipped, pending, current:now, allowed:canSend} = row;
      const label = el('strong', {}, name);
      const stateText = skipped ? '已跳过' : pending ? '等待设备确认' : now ? '当前对象'
        : !safeName(name) ? '名称不支持跳过' : '';
      const button = el('button', {type:'button', disabled:!canSend, 'aria-label':'跳过 ' + name,
        onclick:() => void execute(status => canSkip(status, name), async () => {
          const response = await post('/printer/gcode/script',{script:'EXCLUDE_OBJECT NAME=' + name});
          waiting.add(name);
          return response;
        }, () => '跳过「' + name + '」？本次打印将不再打印这个模型，已打印的部分会保留。')}, '跳过');
      operands.append(el('div', {class:'print-object-row'},
        el('div', {class:'print-object-name'}, label,
          ...(stateText ? [el('small', {class:'muted'}, stateText)] : [])),
        ...(skipped || pending ? [] : [button])));
    }
  }

  render();
  return {root, update(status) {
    if (!sameJob(latest, status) || (!running(job(latest).state) && running(job(status).state))) waiting.clear();
    latest = status;
    for (const name of exclusions(status)) waiting.delete(name);
    render();
  }};
}
