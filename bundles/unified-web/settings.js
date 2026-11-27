const SETTINGS = '/u1/device/firmware/settings';
const CONFIG = '/multiace/api/config';

export function mountSettings(ctx) {
  const {el,tile} = ctx;
  const groups = el('div',{class:'stack'});
  const search = el('input',{type:'search',placeholder:'查找设置，例如：调平、标签、SSH','aria-label':'查找设置'});
  const panel = el('article',{class:'card stack'},el('h2',{},'打印与设备设置'),search,groups);
  ctx.root.replaceChildren(el('section',{class:'stack'},panel,
    el('div',{class:'links'},tile('aceconfig','MultiACE 参数'),tile('devices','ACE 设备与固件'),
      tile('calibration','料管校准'),tile('screen','机身专有设置'),tile('paxx','PAXX 维护与系统升级'),
      tile('multiace','MultiACE 全部功能'))));
  let schema, expanded = new Set(), busy = false;
  search.oninput = () => {
    if (!busy && schema) draw();
  };
  async function load() {
    groups.replaceChildren(el('p',{class:'muted'},'正在读取设备设置…'));
    try {
      const result = await ctx.api(SETTINGS,{signal:ctx.signal});
      if (!result.available || !Array.isArray(result.settings) || !Array.isArray(result.groups)) {
        throw new Error('打印机没有返回可用设置');
      }
      if (!ctx.signal.aborted) { schema=result; draw(); }
    } catch(e) {
      if (!ctx.signal.aborted) groups.replaceChildren(ctx.errorBox(e,load),tile('paxx','打开 PAXX 原生设置'));
    }
  }
  function draw() {
    const query=search.value.trim().toLowerCase();
    const cards = [];
    for (const group of schema.groups) {
      const items = schema.settings.filter(item=>!item.hidden && item.group===group.id &&
        [group.label,item.label,item.id,item.source_label,item.description].join(' ').toLowerCase().includes(query));
      if (!items.length) continue;
      const details=el('details',{class:'settings-group',open:Boolean(query)||expanded.has(group.id)},el('summary',{},group.label));
      details.ontoggle=()=>{ if(!query) { if(details.open) expanded.add(group.id); else expanded.delete(group.id); } };
      for (const item of items) details.append(settingRow(item));
      cards.push(details);
    }
    groups.replaceChildren(...(cards.length ? cards:[el('p',{class:'empty'},query?'未找到匹配的设备设置':'当前固件没有开放可调设置')]));
  }
  function settingRow(item) {
    const options=item.options || [];
    const select=el('select',{'aria-label':item.label});
    if (!options.some(x=>x.value===item.current)) select.append(el('option',{value:'',selected:true,disabled:true},'当前值未知'));
    for (const opt of options.filter(x=>!x.hidden || x.value===item.current)) {
      select.append(el('option',{value:opt.value,selected:opt.value===item.current,disabled:!!opt.hidden},opt.label));
    }
    const extra=el('div',{class:'settings-fields'});
    const apply=el('button',{type:'submit',class:'primary',hidden:true},'应用');
    const form=el('form',{class:'settings-fields'},select,extra,apply);
    select.onchange=()=>{
      extra.replaceChildren();
      const opt=options.find(x=>x.value===select.value);
      apply.hidden=!opt || opt.value===item.current;
      if (!opt || apply.hidden) return;
      for (const field of opt.inputs || []) {
        const input=el('input',{name:field.name,type:field.type==='password'?'password':'text',required:field.required!==false,
          placeholder:field.placeholder || '',autocomplete:'off'});
        extra.append(el('label',{},field.label || field.name,input));
      }
    };
    form.onsubmit=async ev=>{
      ev.preventDefault();
      const opt=options.find(x=>x.value===select.value);
      if (busy || !opt || opt.hidden || opt.value===item.current) return;
      const message=item.label+' → '+opt.label+(opt.confirm?'\n'+opt.confirm:'');
      if (!(await ctx.confirm(message))) return;
      busy=true; panel.inert=true;
      const inputs=Object.fromEntries(Array.from(extra.querySelectorAll('input'),i=>[i.name,i.value]));
      try {
        const result=await ctx.api(SETTINGS,{method:'POST',body:{id:item.id,value:opt.value,inputs,confirm:true}});
        if (result.ok!==true || result.state!=='applied' || result.current!==opt.value) throw new Error(result.message || '尚未确认设置已生效，请刷新核对');
        ctx.notice(result.message || '设备已确认设置');
      } catch(e) {
        ctx.notice(e.status ? e.message : e.message+'。请核对打印机的实际设置。');
      } finally {
        busy=false; panel.inert=false;
        if (!ctx.signal.aborted) await load();
      }
    };
    return el('div',{class:'settings-row'},el('div',{},el('h3',{},item.label),el('p',{},item.description || '')),form);
  }
  load();
}

const PARAMS = {
  feed_speed:'进料速度（mm/s）',retract_speed:'回抽速度（mm/s）',load_length:'进料长度（mm）',
  retract_length:'完整退料长度（mm）',swap_retract_length:'换料回抽长度（mm）',load_retry:'进料重试次数',
  unload_retry:'退料重试次数',extrusion_retry:'挤出重试次数',dryer_temp:'烘干温度（°C）',dryer_duration:'烘干时长（分钟）'
};
function scalarLines(text) {
  const lines=text.split('\n');
  let section='';
  const fields = [];
  for (const [n,line] of lines.entries()) {
    const header=line.match(/^\s*\[([^\]]+)\]/);
    if (header) {section=header[1].trim();continue;}
    if (section!=='ace') continue;
    const m=line.match(/^(\s*([a-z_]+)\s*[:=]\s*)([-+]?(?:\d*\.\d+|\d+))([ \t]*(?:[#;].*)?\r?)$/i);
    if (m && Object.hasOwn(PARAMS,m[2])) fields.push({n,key:m[2],value:m[3],prefix:m[1],suffix:m[4]});
  }
  return fields;
}
export function mountAceConfig(ctx) {
  const {el,tile} = ctx;
  const content=el('div',{class:'stack'}), raw=el('textarea',{rows:18,spellcheck:false,'aria-label':'ace.cfg 完整配置'});
  const form=el('div',{class:'grid'}), save=el('button',{class:'primary',type:'button',disabled:true},'保存配置');
  const rawPanel=el('details',{class:'panel'},el('summary',{},'完整 ace.cfg'),raw);
  const msg=el('p',{role:'status',class:'muted'});
  const refresh=el('button',{type:'button'},'重新读取');
  ctx.root.replaceChildren(el('section',{class:'stack'},el('div',{class:'links'},tile('devices','设备、驱动与固件'),tile('calibration','料管校准')),content));
  let revision='',base='',writing=false;
  function dirty(){save.disabled=writing || !revision || raw.value===base || !raw.value.trim();}
  function renderFields() {
    form.replaceChildren();
    for(const entry of scalarLines(raw.value)) {
      const input=el('input',{type:'number',step:'any',value:entry.value});
      input.oninput=()=>{
        const lines=raw.value.split('\n');
        lines[entry.n]=entry.prefix+input.value+entry.suffix; raw.value=lines.join('\n');
        input.setCustomValidity(input.value.trim() ? '':'请输入数值');dirty();
      };
      form.append(el('label',{class:'settings-fields'},PARAMS[entry.key],input));
    }
  }
  async function load() {
    refresh.disabled=true;save.disabled=true;
    try {
      const data=await ctx.api(CONFIG,{signal:ctx.signal});
      if (ctx.signal.aborted) return;
      if(typeof data.content!=='string' || !data.sha1) throw new Error('当前服务未提供配置版本，无法核对并发修改');
      // TEXTAREA normalizes CRLF. Remember the on-disk convention when saving.
      revision=data.sha1; base=data.content.replace(/\r\n/g,'\n');
      raw.dataset.crlf=String(data.content.includes('\r\n')); raw.value=base;
      renderFields();dirty();
      content.replaceChildren(el('article',{class:'card stack'},el('h2',{},'送料与烘干默认值'),form),rawPanel,el('div',{class:'row'},save,refresh),msg);
    } catch(e) {
      if(!ctx.signal.aborted) {
        msg.textContent=e.message;
        if(!revision) content.replaceChildren(ctx.errorBox(e,load));
      }
    } finally {refresh.disabled=false;dirty();}
  }
  raw.oninput=()=>{renderFields();dirty();};
  refresh.onclick=async()=>{if (raw.value!==base && !await ctx.confirm('放弃未保存的修改并重新读取？')) return; await load();};
  save.onclick=async()=>{
    if (writing || save.disabled) return;
    for (const input of form.querySelectorAll('input')) if (!input.reportValidity()) return;
    if (!await ctx.confirm('保存当前 ace.cfg？更改会写入打印机，部分参数在重启后生效。')) return;
    const value=raw.value;
    const disk=raw.dataset.crlf==='true' ? value.replace(/\n/g,'\r\n') : value;
    writing=true;content.inert=true;dirty();
    try {
      const r=await ctx.api(CONFIG,{method:'PUT',body:{content:disk,base_sha1:revision,restart_klipper:false}});
      const check=await ctx.api(CONFIG);
      if (!r.sha1 || r.sha1!==check.sha1 || check.content!==disk) throw new Error('未能从设备读回相同的配置，请重新读取');
      revision=check.sha1;base=value;msg.textContent='配置已保存并读回确认。';
    } catch(e) {
      msg.textContent=e.status===409?'设备上的配置已被修改。请保留所需改动，重新读取后再保存。':'保存结果未确认：'+e.message;
    } finally {writing=false;content.inert=false;dirty();}
  };
  load();
}
