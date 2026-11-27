import { mountMaterial } from './material.js';
import { mountOverview, mountControl } from './control.js';
import { mountSettings, mountAceConfig } from './settings.js';

const page = document.querySelector('#page');
const login = document.querySelector('#login');
const dialog = document.querySelector('#confirm');
const titles = {
  overview:'总览', control:'控制', material:'耗材', jobs:'打印任务', settings:'设置',
  aceconfig:'MultiACE 参数', multiace:'MultiACE 完整面板', printhub:'上传与打印预检',
  devices:'ACE 设备与固件', calibration:'料管校准', paxx:'PAXX 维护与系统升级',
  screen:'机身屏幕', fluidd:'打印机工作台'
};
const parents = {aceconfig:'settings', devices:'settings', calibration:'settings', multiace:'material',
  printhub:'jobs', paxx:'settings', screen:'control', fluidd:'control'};
let selectedHead = 0;
let teardown = () => {};
let owner;
let noticeTimer;

function notice(message) {
  const box = document.querySelector('#notice');
  box.textContent = String(message);
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { box.textContent = ''; }, 6500);
}
function el(tag, attrs = {}, ...children) {
  const item = document.createElement(tag);
  for (const [key,value] of Object.entries(attrs)) {
    if (value === undefined || value === null) continue;
    if (key === 'class') item.className = value;
    else if (key.startsWith('on') && typeof value === 'function') item.addEventListener(key.slice(2),value);
    else if (key in item && !key.startsWith('aria-')) item[key] = value;
    else item.setAttribute(key, value);
  }
  item.append(...children);
  return item;
}
function errorText(data) {
  const detail = data?.detail ?? data?.error ?? data?.message;
  if (typeof detail === 'string') {
    if (detail.startsWith('{')) {
      try { return errorText(JSON.parse(detail)); } catch {}
    }
    return detail;
  }
  return detail?.message || '';
}
function accessToken() {
  try { return localStorage.getItem('user-token-' + location.host.replace(/[^a-zA-Z0-9]/g,'_')); }
  catch { return null; }
}
async function response(path, options = {}) {
  const url = new URL(path, location.href);
  if (url.origin !== location.origin) throw new Error('需要打印机同源地址');
  const headers = new Headers(options.headers);
  const token = accessToken();
  if (token) headers.set('Authorization','Bearer ' + token);
  let body = options.body;
  if (body != null && typeof body === 'object' && !(body instanceof FormData) && !(body instanceof Blob)) {
    body = JSON.stringify(body);
    headers.set('Content-Type','application/json');
  }
  const r = await fetch(url, {...options, headers, body, cache:'no-store', credentials:'same-origin', redirect:'error'});
  if (!r.ok && r.status !== 304) {
    if (r.status === 401 || r.status === 403) login.hidden = false;
    let reason = '';
    try { reason = errorText(await r.json()); } catch {}
    const e = new Error(reason || ({401:'请先登录打印机',403:'当前账户无权执行此操作',502:'服务未连接',503:'服务暂不可用'}[r.status]) || ('HTTP ' + r.status));
    e.status = r.status;
    throw e;
  }
  return r;
}
async function api(path, options = {}) {
  const r = await response(path, options);
  const result = await r.json();
  if (result?.error) throw new Error(errorText(result) || '操作失败');
  return result;
}
function confirmAction(text) {
  if (dialog.open) return Promise.resolve(false);
  if (!dialog.showModal) return Promise.resolve(window.confirm(text));
  dialog.returnValue = 'cancel';
  dialog.querySelector('p').textContent = text;
  return new Promise(resolve => {
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'ok'), {once:true});
    dialog.showModal();
  });
}
function tile(name, label) {
  return el('a',{class:'tile',href:'#' + name},el('strong',{},label),el('span',{'aria-hidden':'true'},'›'));
}
function errorBox(error, retry) {
  return el('div',{class:'error-box row',role:'status'},el('span',{},error.message || String(error)),
    el('button',{type:'button',onclick:retry},'重试'));
}
const panelSource = {multiace:'/multiace/',printhub:'/multiace/',devices:'/multiace/',
  calibration:'/multiace/',paxx:'/firmware-config/',fluidd:'/?workspace=1'};
const initialTab = {printhub:'control',devices:'devices',calibration:'calibration',multiace:'dashboard'};

async function integratedPanel(ctx, key) {
  const path = panelSource[key];
  const wrap = el('section',{class:'stack'});
  ctx.root.replaceChildren(wrap);
  const status = el('p',{class:'muted'},'正在打开…');
  wrap.append(status);
  try {
    // Probe using the same credentials a normal frame navigation can carry.
    // A successful API fetch with a Bearer header alone does not authorize an iframe.
    const r = await fetch(path,{credentials:'same-origin',cache:'no-store',signal:ctx.signal});
    if (!r.ok || !r.headers.get('content-type')?.includes('text/html')) {
      if (r.status === 401 || r.status === 403) {
        if (path === '/multiace/' && accessToken()) {
          const session = await response(path,{signal:ctx.signal});
          if (session.headers.get('content-type')?.includes('text/html')) {
            throw new Error('打印机已确认登录，但当前网络要求每次请求携带登录凭据，无法嵌入完整 MultiACE 面板。可返回「耗材」或「设置」使用对应控制项。');
          }
        }
        login.hidden = false;
      }
      throw new Error('原面板未能打开（HTTP ' + r.status + '）。');
    }
    if (ctx.signal.aborted) return;
    if (initialTab[key]) {
      try { localStorage.setItem('multiace.tab', initialTab[key]); } catch {}
    }
    wrap.replaceChildren(el('iframe',{class:'frame',title:titles[key],src:path,allow:'fullscreen'}));
  } catch (e) {
    if (ctx.signal.aborted) return;
    wrap.replaceChildren(errorBox(e,()=>integratedPanel(ctx,key)),el('div',{class:'links'},
      tile('screen','通过机身屏幕操作'),tile('aceconfig','MultiACE 参数')));
  }
}

async function mountJobs(ctx) {
  const wrap = el('section',{class:'stack'});
  const queue = el('div'), history = el('div');
  ctx.root.replaceChildren(wrap);
  wrap.append(el('div',{class:'links'},tile('printhub','上传 G-code 与打印预检'),tile('fluidd','文件管理')),
    el('article',{class:'card stack'},el('h2',{},'待打印'),queue),
    el('article',{class:'card stack'},el('h2',{},'最近打印'),history),
    el('button',{type:'button',onclick:refresh},'刷新任务'));
  let seq = 0, sending = false;
  async function refresh() {
    const revision = ++seq;
    await Promise.all([[queue,'/multiace/api/queue','queue'],[history,'/multiace/api/history?limit=12','jobs']].map(async ([section,path,field]) => {
      try {
        const data = await ctx.api(path);
        if (ctx.signal.aborted || revision !== seq) return;
        const rows = data[field];
        if (!Array.isArray(rows)) throw new Error('任务数据格式异常');
        section.replaceChildren();
        if (!rows.length) section.append(el('p',{class:'muted'},field==='queue'?'队列为空':'暂无打印记录'));
        for (const row of rows) {
          const line = el('div',{class:'settings-row'},el('strong',{},row.filename || '未命名文件'));
          if (field === 'jobs') {
            const states = {completed:'已完成',cancelled:'已取消',interrupted:'已中断',error:'失败',in_progress:'打印中',standby:'待机'};
            line.append(el('span',{class:'muted'},states[row.status] || row.status || '状态未知'));
          } else {
            const ready = Array.isArray(row.changes) && !row.changes.length && typeof row.id === 'string';
            const b = el('button',{type:'button',disabled:!ready || sending},ready?'开始打印':row.changes?.length?'请重新匹配耗材':'等待读取料槽');
            b.onclick = async () => {
              if (sending || !(await ctx.confirm('开始打印「' + row.filename + '」？')) || ctx.signal.aborted) return;
              sending = true; b.disabled = true;
              try {
                const r = await ctx.api('/multiace/api/queue/' + encodeURIComponent(row.id) + '/launch',{method:'POST'});
                if (!r.ok) throw new Error(r.message || '打印机未接受该任务');
                ctx.notice('打印机已接受任务');
              } catch(e) { ctx.notice(e.message); }
              finally { sending = false; if (!ctx.signal.aborted) refresh(); }
            };
            line.append(b);
          }
          section.append(line);
        }
      } catch(e) { if (!ctx.signal.aborted && revision === seq) section.replaceChildren(errorBox(e,refresh)); }
    }));
  }
  await refresh();
}

function mountScreen(ctx) {
  // Use the printer's existing touch client. Its own authentication, input
  // coordinate conversion and long-press behavior apply inside the same tab.
  const wrap=el('section',{class:'stack'});
  const link=tile('settings','远程屏幕设置');
  ctx.root.replaceChildren(wrap);
  async function show() {
    try {
      const r=await ctx.response('/screen/snapshot',{signal:ctx.signal});
      if (!r.headers.get('Content-Type')?.startsWith('image/')) throw new Error('没有收到打印机画面');
      if (!ctx.signal.aborted) wrap.replaceChildren(el('iframe',{class:'frame',title:'机身触摸屏',src:'/screen/'}),link);
    } catch(e) {
      if (!ctx.signal.aborted) wrap.replaceChildren(errorBox(new Error('机身屏幕不可用：'+e.message),show),link);
    }
  }
  show();
}
const views = {overview:mountOverview,control:mountControl,material:mountMaterial,
  settings:mountSettings,aceconfig:mountAceConfig,jobs:mountJobs,screen:mountScreen};
function render() {
  const wanted = location.hash.slice(1);
  const key = Object.hasOwn(titles,wanted) ? wanted : 'overview';
  teardown();
  if (dialog.open) dialog.close('cancel');
  const abort = new AbortController(), cleanup = [];
  const slot = el('div');
  page.replaceChildren(slot);
  owner = slot;
  teardown = () => {
    abort.abort();
    for (const fn of cleanup.splice(0)) { try { fn(); } catch(e) { console.error(e); } }
  };
  document.title = titles[key] + ' · U1';
  document.querySelector('#title').textContent=titles[key];
  const parent = document.querySelector('#parent');
  parent.hidden = !parents[key];
  if (!parent.hidden) { parent.href = '#'+parents[key]; parent.textContent='‹ '+titles[parents[key]]; }
  document.querySelector('#screen-link').hidden = key==='screen';
  document.querySelectorAll('#nav a').forEach(a=>{
    if (a.hash.slice(1)===(parents[key] || key)) a.setAttribute('aria-current','page'); else a.removeAttribute('aria-current');
  });
  const ctx = {root:slot, response, api, el, tile, errorBox, signal:abort.signal, notice,
    onCleanup:fn => cleanup.push(fn), open:name=>{if(Object.hasOwn(titles,name)) location.hash=name;},
    confirm:async msg=>owner===slot && !abort.signal.aborted && await confirmAction(msg) && !abort.signal.aborted,
    get head(){return selectedHead;},set head(value){if (Number.isInteger(value)&&value>=0&&value<4) selectedHead=value;}};
  Promise.resolve().then(()=>(views[key] || integratedPanel)(ctx,key)).catch(e=>{
    if (!abort.signal.aborted) slot.replaceChildren(errorBox(e,render));
  });
  window.scrollTo(0,0);
}
document.querySelector('#origin').textContent=location.host;
let loginReturn = '';
document.querySelector('#open-login').onclick = () => {
  if (location.hash !== '#fluidd') loginReturn = location.hash || '#overview';
};
document.querySelector('#retry-login').onclick = () => {
  login.hidden = true;
  const target = loginReturn;
  loginReturn = '';
  if (location.hash === '#fluidd' && target && target !== location.hash) location.hash = target;
  else render();
};
window.addEventListener('hashchange',render);
// Browsers retain the fragment on the bare printer address redirect. Restore
// older Fluidd and Mainsail hash routes inside the original frontend.
if (location.hash.startsWith('#/')) location.replace('/?workspace=1' + location.hash);
else render();
