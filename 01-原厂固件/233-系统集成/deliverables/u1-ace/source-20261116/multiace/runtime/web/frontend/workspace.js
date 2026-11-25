/* Presentation and read-only identity queries, separate from feeder control. */
(function (root) {
  'use strict';
  function routes(state) {
    const heads = state.toolheads || [], result = [];
    for (const ace of state.aces || []) for (const slot of ace.slots || []) {
      let head = null;
      if (state.mode === 'multi') head = heads.find(h => h.idx === slot.idx && !h.manual && !h.feeder && h.module_exist !== false);
      if (state.mode === 'head') head = heads.find(h =>
        !h.manual && !h.feeder && h.module_exist !== false &&
        (state.ace_heads || []).includes(h.idx) && Number(state.head_ace?.[h.idx] ?? h.idx) === ace.idx);
      // Recorded origin is stronger than the configured route. It is still
      // not proof of filament at the extruder; the sensor decides that.
      const recorded = heads.find(h => h.head_source_known && h.ace === ace.idx && h.slot === slot.idx);
      if (recorded) head = recorded;
      if (!head || state.mode === 'normal') continue;
      const known = !!recorded;
      result.push({ace: ace.idx, slot: slot.idx, head: head.idx,
        loaded: ace.connected === true && head.module_exist !== false && known && !head.load_failed && head.filament_at_extruder === true,
        known, failed: known && !!head.load_failed, offline: ace.connected !== true,
        color: /^#[0-9a-f]{6}$/i.test(slot.color || '') ? slot.color : '#9c9386'});
    }
    return result;
  }
  function headEvidence(ace, slot, head, route) {
    if (!ace || ace.connected !== true || !head || head.module_exist === false || typeof head.filament_at_extruder !== 'boolean') return 'unknown';
    if (!head.filament_at_extruder) return 'not_detected';
    if (route?.loaded) return 'confirmed';
    if (!head.head_source_known) return 'sensor_source_unknown';
    if (head.ace === ace.idx && head.slot === slot.idx && head.load_failed) return 'load_failed';
    return 'sensor_other_source';
  }
  function parseIdentity(entries, after, aceIdx) {
    // Find THIS query's command, then its response. Never reuse an old
    // version or attribute an overlapping query for another ACE to it.
    let armed = false;
    for (const entry of entries) {
      if (!(entry.time > after)) continue;
      const message = String(entry.message || '');
      if (entry.type === 'command' && /\bA_INFO\b/.test(message)) {
        if (armed) throw new Error('identity_overlap');
        armed = message.trim() === `A_INFO ACE=${aceIdx}`;
        continue;
      }
      if (!armed || entry.type !== 'response') continue;
      const match = message.match(/\[A get_info\]\s*(\{.*\})/s);
      if (!match) continue;
      const response = JSON.parse(match[1]);
      if (response.msg !== 'success' || response.code !== 0) throw new Error('identity_failed');
      const info = response.result || {};
      if (!info.firmware) throw new Error('identity_empty');
      return {model: String(info.model || ''), firmware: String(info.firmware),
        boot_version: String(info.boot_version || '')};
    }
    return null;
  }
  function create(ctx) {
    const {ref, reactive, computed, watch, nextTick, onMounted, onUnmounted} = Vue;
    const {state, tab, t, dispIdx, language, reloadState} = ctx;
    const topologyEl = ref(null), paths = ref([]), viewBox = ref('0 0 100 100');
    const slotEls = {}, headEls = {}, identities = reactive({}), infoBusy = ref(false);
    const infoError = ref(''), infoAttempted = ref(false), updatedAt = ref(null);
    const selectedAce = ref(null), dashboardSlot = ref(0), stateUpdatedAt = ref(null), freshnessNow = ref(Date.now());
    const stateFresh = computed(() => stateUpdatedAt.value !== null && freshnessNow.value - stateUpdatedAt.value < 15000);
    const markUpdated=()=>{stateUpdatedAt.value=Date.now();};
    function selectDashboard(slot) { dashboardSlot.value=slot.idx; if (window.innerWidth<=1100) nextTick(()=>document.querySelector('.dash-detail')?.scrollIntoView({behavior:'smooth',block:'start'})); }
    function backToRows() { document.querySelector('.dash-main')?.scrollIntoView({behavior:'smooth',block:'start'}); }
    const selectedSlot = computed(() => (displayedAce.value?.slots || []).find(s => s.idx === dashboardSlot.value) || null);
    function slotOrigin(slot) {
      if (slot.source === 'manual' || slot.source === 'set' || slot.source === 'override' || slot.ident_src === 'manual') return language.value === 'zh' ? '手动' : 'Manual';
      if (slot.source === 'rfid' || slot.ident_src === 'rfid' || slot.tag_format) return language.value === 'zh' ? '标签' : 'Tag';
      return language.value === 'zh' ? '未确认' : 'Unconfirmed';
    }
    function targetHead(slot) { const route=slotRoute(displayedAce.value?.idx,slot.idx); return route ? state.toolheads.find(h => h.idx===route.head) : null; }
    function selectedHead() { return selectedSlot.value ? targetHead(selectedSlot.value) : null; }
    function routeLoaded(slot) { return !!slotRoute(displayedAce.value?.idx,slot.idx)?.loaded; }
    function slotHeadEvidence(slot) { return headEvidence(displayedAce.value,slot,targetHead(slot),slotRoute(displayedAce.value?.idx,slot.idx)); }

    const wt = (key, params) => t('ui.workspace.' + key, params);
    const routeList = computed(() => routes(state));
    const displayedAce = computed(() => (state.aces || []).find(a => a.idx === selectedAce.value)
      || (state.aces || []).find(a => a.idx === state.active_device) || (state.aces || [])[0] || null);
    const displayRoutes = computed(() => routeList.value.filter(r => r.ace === displayedAce.value?.idx));
    const onlineCount = computed(() => state.aces.filter(a => a.connected === true).length);
    const loadedCount = computed(() => routeList.value.filter(r => r.loaded).length);
    function slotRoute(ai, si) { return routeList.value.find(r => r.ace === ai && r.slot === si); }
    function routeText(ai, si) {
      const route = slotRoute(ai, si);
      if (!route) return wt('unassigned');
      return `T${dispIdx(route.head)} · ${wt(route.offline ? 'offline' : route.failed ? 'load_failed' : route.loaded ? 'at_extruder' : 'configured')}`;
    }
    function headSource(head) {
      if (head.head_source_known && head.ace != null && head.slot != null)
        return `ACE ${dispIdx(head.ace)} / ${wt('slot')} ${dispIdx(head.slot)}`;
      if (head.manual || head.feeder) return wt('external_feeder');
      if (head.filament_at_extruder) return wt('unknown_source');
      const configured = displayRoutes.value.filter(r => r.head === head.idx);
      if (configured.length === 1) return `ACE ${dispIdx(configured[0].ace)} / ${wt('slot')} ${dispIdx(configured[0].slot)}`;
      if (configured.length > 1) return wt('configured_from', {n:dispIdx(configured[0].ace)});
      return wt('unassigned');
    }
    function headState(head) {
      if (head.module_exist === false) return wt('offline');
      if (head.load_failed) return wt('load_failed');
      if (head.filament_at_extruder) return wt('at_extruder');
      return wt('not_loaded');
    }
    function materialText(slot) { return slot.state === 'empty' ? wt('empty') : slot.material || wt('unidentified'); }
    function identity(ace) {
      const item = identities[ace.idx];
      return item && item.path === ace.serial_path && ace.connected === true ? item : {};
    }
    function infoValue(ace, key) { return identity(ace)[key] || ace[key] || wt('not_read'); }
    function timeText(value) {
      return value ? new Date(value).toLocaleTimeString(language.value === 'zh' ? 'zh-CN' : 'en-GB', {hour12: false}) : '—';
    }
    async function getJson(url, options) {
      const response = await fetch(url, {...options, signal: AbortSignal.timeout(10000), cache: 'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    }
    async function readIdentities() {
      if (infoBusy.value) return;
      infoBusy.value = true; infoError.value = ''; infoAttempted.value = true;
      try {
        await reloadState();
        for (const ace of state.aces.filter(a => a.connected === true && a.protocol === 'v2')) {
          try {
            const before = await getJson('/server/gcode_store?count=100');
            const cursor = Math.max(0, ...(before.result?.gcode_store || []).map(e => Number(e.time) || 0));
            const path = ace.serial_path;
            await getJson('/multiace/api/macro', {method: 'POST', headers: {'Content-Type':'application/json'},
              body: JSON.stringify({name:'A_INFO', args:{ACE:ace.idx}})});
            const after = await getJson('/server/gcode_store?count=200');
            const info = parseIdentity(after.result?.gcode_store || [], cursor, ace.idx);
            if (!info) throw new Error('identity_empty');
            const current = state.aces.find(a => a.idx === ace.idx);
            if (!current || current.connected !== true || current.serial_path !== path) throw new Error('identity_changed');
            identities[ace.idx] = {...info, path, time:Date.now()};
          } catch (error) {
            delete identities[ace.idx];
            infoError.value += `${infoError.value ? ' / ' : ''}ACE ${dispIdx(ace.idx)}: ${wt('read_failed')}`;
          }
        }
        updatedAt.value = Date.now();
      } catch (error) { infoError.value = wt('read_failed'); }
      finally { infoBusy.value = false; }
    }
    function openDevice(ace) { if (ace) selectedAce.value = ace.idx; tab.value = 'devices'; }
    function setTopologySlot(ai, si, el) { if (el) slotEls[`${ai}_${si}`] = el; else delete slotEls[`${ai}_${si}`]; }
    function setTopologyHead(hi, el) { if (el) headEls[hi] = el; else delete headEls[hi]; }
    function redraw() {
      const box = topologyEl.value?.getBoundingClientRect();
      if (!box || !box.width || !box.height) return;
      viewBox.value = `0 0 ${box.width} ${box.height}`;
      paths.value = displayRoutes.value.flatMap(route => {
        const start = slotEls[`${route.ace}_${route.slot}`]?.getBoundingClientRect();
        const end = headEls[route.head]?.getBoundingClientRect();
        if (!start || !end) return [];
        const x1 = start.right - box.left, y1 = start.top + start.height / 2 - box.top;
        const x2 = end.left - box.left, y2 = end.top + end.height / 2 - box.top;
        const bend = Math.max(12, (x2 - x1) * .52);
        return [{...route, d:`M ${x1} ${y1} C ${x1+bend} ${y1}, ${x2-bend} ${y2}, ${x2} ${y2}`}];
      });
    }
    function schedule() { nextTick(() => requestAnimationFrame(redraw)); }
    watch([displayRoutes, displayedAce, tab], schedule, {deep:true});
    watch(() => state.aces.map(a => `${a.idx}:${a.connected}:${a.serial_path}`).join('|'), () => {
      for (const key of Object.keys(identities)) {
        const ace = state.aces.find(a => String(a.idx) === key);
        if (!ace || ace.connected !== true || ace.serial_path !== identities[key].path) delete identities[key];
      }
    });
    watch(tab, value => { window.scrollTo({top:0}); if (value === 'devices' && !infoAttempted.value) readIdentities(); });
    let observer, freshnessTimer;
    onMounted(() => {
      freshnessTimer=setInterval(()=>{freshnessNow.value=Date.now();},1000);
      observer = new ResizeObserver(schedule);
      if (topologyEl.value) observer.observe(topologyEl.value);
      window.addEventListener('resize', schedule); window.addEventListener('multiace-state-received',markUpdated);
      schedule();
      if (tab.value === 'devices') readIdentities();
    });
    onUnmounted(() => { clearInterval(freshnessTimer); observer?.disconnect(); window.removeEventListener('resize', schedule); window.removeEventListener('multiace-state-received',markUpdated); });
    return {wt, topologyEl, topologyPaths:paths, topologyViewBox:viewBox, setTopologySlot, setTopologyHead,
      topologyRoutes:routeList, selectedAce, displayedAce, onlineCount, loadedCount, slotRoute, routeText, dashboardSlot, selectedSlot, stateUpdatedAt, stateFresh, slotOrigin, targetHead, selectedHead, routeLoaded, slotHeadEvidence, selectDashboard, backToRows,
      materialText, headSource, headState, infoValue, identity, infoBusy, infoError, updatedAt,
      timeText, readIdentities, openDevice};
  }
  const api = {routes, headEvidence, parseIdentity, create};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MultiaceWorkspace = api;
})(typeof window !== 'undefined' ? window : this);
