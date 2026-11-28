import { createPrintActions } from './print-actions.js';

const stats = 'print_stats=filename,state,print_duration,total_duration,filament_used,info,message';
export const printObjects = 'webhooks=state&' + stats
  + '&display_status=progress,message&virtual_sdcard=progress,file_position,file_size'
  + '&exclude_object=objects,excluded_objects,current_object';
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

function thumbnailPath(file, data) {
  if (!Array.isArray(data?.thumbnails)) return '';
  const relative = path => typeof path === 'string'
    && path.split('/').every(part => part && part !== '.' && part !== '..' && !/[\\\x00-\x1f]/.test(part));
  if (!relative(file)) return '';
  const options = data.thumbnails.filter(thumb => relative(thumb?.relative_path)
    && /\.(png|jpe?g|webp)$/i.test(thumb.relative_path)
    && (!number(thumb.size) || thumb.size <= 3 * 1024 * 1024));
  if (!options.length) return '';
  options.sort((a,b) => (b.width || 0) - (a.width || 0));
  const parts = file.split('/').slice(0,-1).concat(options[0].relative_path.split('/'));
  return '/server/files/gcodes/' + parts.map(part => encodeURIComponent(part)).join('/');
}

export function createPrintDetails(ctx, options) {
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
  const preview = el('figure', {class:'print-preview', hidden:true});
  const previewState = el('span', {class:'muted'}, '正在读取预览…');
  const previewButton = el('button', {type:'button', class:'print-preview-trigger',
    title:'打开模型俯视选图', 'aria-label':'打开模型俯视选图', onclick:() => actions.openPicker()});
  const body = el('div', {class:'print-main'}, preview, el('div', {class:'print-content'},
    el('div', {class:'print-summary'}, el('div', {class:'print-identity'}, badge, name), percent),
    progress, metrics, message));
  const actions = createPrintActions(ctx, refresh, options);
  root.append(body, actions.root);

  let latest = {}, file = '', previousState, generation = 0, metadata, imageUrl;
  let eventtime = -Infinity;

  function releasePreview() {
    preview.replaceChildren();
    if (imageUrl) URL.revokeObjectURL(imageUrl);
    imageUrl = undefined;
  }
  ctx.onCleanup(releasePreview);

  async function loadPreview(filename, data, request) {
    const found = thumbnailPath(filename, data);
    const proxy = '/multiace/api/thumbnail?' + new URLSearchParams({path:filename});
    for (const path of [found, proxy].filter(Boolean)) {
      if (request !== generation || ctx.signal.aborted) return;
      let source;
      try {
        const response = await ctx.response(path, {signal:ctx.signal});
        const blob = await response.blob();
        if (!blob.type.startsWith('image/') || !blob.size || blob.size > 3 * 1024 * 1024)
          throw new Error('Invalid thumbnail');
        source = URL.createObjectURL(blob);
        const image = el('img', {alt:'当前模型预览', src:source});
        await image.decode();
        if (request !== generation || ctx.signal.aborted) return;
        releasePreview();
        previewButton.replaceChildren(image);
        preview.append(previewButton);
        imageUrl = source;
        source = undefined;
        return;
      } catch {
        // Try the existing same-origin thumbnail proxy if the file URL fails.
      } finally {
        if (source) URL.revokeObjectURL(source);
      }
    }
    if (request === generation && !ctx.signal.aborted) previewState.textContent = '暂无模型预览';
  }

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
    previewButton.disabled = !running(state);
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

  function update(status, timestamp) {
    if (Number.isFinite(timestamp) && timestamp < eventtime) return;
    if (Number.isFinite(timestamp)) eventtime = timestamp;
    else if (!status.webhooks?.state) eventtime = -Infinity;
    const previousDuration = latest.print_stats?.total_duration;
    latest = status;
    actions.update(status);
    const job = status.print_stats || {};
    const next = Object.hasOwn(states, job.state) && typeof job.filename === 'string' ? job.filename : '';
    const restarted = running(job.state) && (!running(previousState) ||
      (number(previousDuration) && number(job.total_duration) && job.total_duration < previousDuration));
    const changed = file !== next || restarted;
    previousState = job.state;
    if (changed) {
      file = next;
      metadata = null;
      const request = ++generation;
      releasePreview();
      preview.hidden = !file;
      if (file) {
        previewState.textContent = '正在读取预览…';
        preview.append(previewState);
        ctx.api('/server/files/metadata?' + new URLSearchParams({filename:file}), {signal:ctx.signal})
          .then(data => data.result || null).catch(() => null).then(data => {
            if (request !== generation || ctx.signal.aborted) return;
            metadata = data;
            render();
            void loadPreview(next, data, request);
          });
      }
    }
    render();
  }

  async function refresh() {
    try {
      const data = await ctx.api('/printer/objects/query?' + printObjects, {signal:ctx.signal});
      if (!ctx.signal.aborted) update(data.result?.status || {}, data.result?.eventtime);
      return latest;
    } catch (error) {
      if (!ctx.signal.aborted) update({});
      throw error;
    }
  }

  render();
  return {root, update, refresh};
}
