const stats = 'print_stats=filename,state,print_duration,total_duration,filament_used,info,message';
export const printObjects = stats + '&display_status=progress,message&virtual_sdcard=progress,file_position,file_size';
const states = {printing:'打印中', paused:'已暂停', complete:'打印完成', cancelled:'已取消', error:'打印失败'};
const running = state => state === 'printing' || state === 'paused';
const number = value => Number.isFinite(value) && value >= 0;
const count = value => Number.isInteger(value) && value > 0;
const missing = '—';

function duration(value) {
  if (!number(value)) return missing;
  const minutes = Math.floor(value / 60);
  if (minutes < 1 && value > 0) return '不到 1 分钟';
  if (minutes < 60) return minutes + ' 分钟';
  return Math.floor(minutes / 60) + ' 小时 ' + minutes % 60 + ' 分钟';
}

function ratio(...candidates) {
  return candidates.find(p => number(p) && p <= 1);
}

function remainingTime(elapsed, progress, metadata) {
  if (elapsed >= 30 && progress >= 0.03 && progress < 1) return elapsed * (1 / progress - 1);
  if (metadata?.estimated_time > elapsed) return metadata.estimated_time - elapsed;
  return undefined;
}

export function createPrintDetails(ctx) {
  const {el} = ctx;
  const root = el('div', {class:'print-details'});
  const name = el('strong', {class:'print-file'});
  const badge = el('span', {class:'print-state'});
  const percent = el('strong', {class:'print-percent'});
  const progress = el('progress', {max:100, 'aria-label':'打印进度'});
  const message = el('p', {class:'print-message muted', hidden:true});
  const metrics = el('dl', {class:'print-metrics'});
  const values = new Map();
  for (const label of ['层数', '已用时间', '预计剩余', '预计完成', '已用耗材']) {
    const value = el('dd');
    metrics.append(el('div', {}, el('dt', {}, label), value));
    values.set(label, value);
  }
  root.append(el('div', {class:'print-summary'},
    el('div', {class:'print-identity'}, badge, name), percent), progress, metrics, message);
  let latest = {}, file = '', previousState, generation = 0, metadata;

  function render() {
    const job = latest.print_stats || {};
    const sd = latest.virtual_sdcard || {};
    const state = job.state;
    const p = state === 'complete' ? 1 : ratio(latest.display_status?.progress,
      sd.progress, sd.file_size > 0 ? sd.file_position / sd.file_size : undefined);
    const elapsed = number(job.print_duration) ? job.print_duration : undefined;
    const remaining = running(state) ? remainingTime(elapsed ?? 0, p, metadata) : undefined;
    const current = job.info?.current_layer;
    const total = count(job.info?.total_layer) ? job.info.total_layer : metadata?.layer_count;
    const totalDuration = number(job.total_duration) ? job.total_duration : elapsed;

    badge.textContent = states[state] || (state === 'standby' ? '待机' : '状态不可用');
    badge.dataset.state = state || '';
    name.textContent = file || (state === 'standby' ? '等待打印任务' : '未读取到打印任务');
    progress.hidden = percent.hidden = !file || p === undefined;
    if (!progress.hidden) {
      progress.value = p * 100;
      percent.textContent = Number((p * 100).toFixed(1)) + '%';
    }

    metrics.hidden = !file;
    values.get('层数').textContent = count(total)
      ? (count(current) ? current : missing) + ' / ' + total
      : count(current) ? '第 ' + current + ' 层' : missing;
    values.get('已用时间').textContent = duration(totalDuration);
    values.get('预计剩余').textContent = duration(remaining);
    values.get('预计完成').textContent = state === 'printing' && number(remaining)
      ? new Date(Date.now() + remaining * 1000).toLocaleString('zh-CN',
          {month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit', hour12:false})
      : missing;
    values.get('已用耗材').textContent = number(job.filament_used)
      ? (job.filament_used / 1000).toFixed(2) + ' m' : missing;

    const detail = job.message || (running(state) ? latest.display_status?.message : '');
    message.hidden = !file || !detail;
    message.textContent = detail || '';
  }

  function update(status) {
    latest = status;
    const job = status.print_stats || {};
    const next = Object.hasOwn(states, job.state) && typeof job.filename === 'string' ? job.filename : '';
    const changed = file !== next || (running(job.state) && !running(previousState));
    previousState = job.state;
    if (changed) {
      file = next;
      metadata = null;
      const request = ++generation;
      if (file) {
        ctx.api('/server/files/metadata?' + new URLSearchParams({filename:file}), {signal:ctx.signal}).then(data => {
          if (request !== generation || ctx.signal.aborted) return;
          metadata = data.result || {};
          render();
        }).catch(() => {}); // Live telemetry still works without slicer metadata.
      }
    }
    render();
  }

  render();
  return {root, update};
}
