const webcamApi = '/multiace/api/webcam/';
const screenUrl = '/screen/snapshot';

function makeMedia(ctx, title, initiallyOpen, connect, link) {
  const {el} = ctx;
  const status = el('p', {class:'monitor-status', role:'status'});
  const view = el('div', {class:'monitor-view'});
  const toggle = el('button', {type:'button', onclick:() => {
    wanted = !wanted;
    sync();
  }});
  const card = el('section', {class:'card monitor-card'},
    el('header', {class:'monitor-heading'}, el('h3', {}, title), toggle), view,
    el('footer', {class:'monitor-footer'}, status, ...(link ? [link] : [])));
  let wanted = initiallyOpen;
  let session;

  function report(text, state) {
    status.textContent = text;
    status.dataset.state = state;
  }

  function stop() {
    session?.abort();
    session = undefined;
    view.replaceChildren();
  }

  function sync() {
    toggle.textContent = wanted ? '关闭画面' : '显示画面';
    if (!wanted || document.hidden || ctx.signal.aborted) {
      stop();
      report(wanted ? '后台已暂停' : '画面已关闭', 'idle');
      return;
    }
    if (session) return;
    session = new AbortController();
    const signal = session.signal;
    report('正在连接…', 'loading');
    connect(view, report, signal).catch(error => {
      if (!signal.aborted) report(error.message || '画面暂不可用', 'error');
    });
  }

  document.addEventListener('visibilitychange', sync);
  ctx.onCleanup(() => {
    document.removeEventListener('visibilitychange', sync);
    stop();
  });
  sync();
  return card;
}

async function pollFrames(ctx, view, report, signal, path, alt, label, interval) {
  let timer, lastUrl, etag;
  signal.addEventListener('abort', () => {
    clearTimeout(timer);
    if (lastUrl) URL.revokeObjectURL(lastUrl);
  }, {once:true});
  async function refresh() {
    let delay = interval;
    let nextUrl;
    const request = new AbortController();
    const cancel = () => request.abort();
    const timeout = setTimeout(cancel, 5000);
    signal.addEventListener('abort', cancel, {once:true});
    try {
      const response = await ctx.response(path, {
        signal:request.signal,
        headers:etag ? {'If-None-Match':etag} : {}
      });
      if (response.status !== 304 || !lastUrl) {
        const blob = await response.blob();
        if (!blob.type.startsWith('image/') || !blob.size) throw new Error('未收到画面');
        nextUrl = URL.createObjectURL(blob);
        const image = ctx.el('img', {alt, src:nextUrl});
        await image.decode();
        if (signal.aborted) return;
        view.replaceChildren(image);
        if (lastUrl) URL.revokeObjectURL(lastUrl);
        lastUrl = nextUrl;
        nextUrl = undefined;
        etag = response.headers.get('ETag');
      }
      if (!signal.aborted) report(label, 'ready');
    } catch {
      if (!signal.aborted) report(lastUrl ? '更新中断，保留最后画面' : '画面暂不可用', 'error');
      delay = 5000;
    } finally {
      clearTimeout(timeout);
      signal.removeEventListener('abort', cancel);
      if (nextUrl) URL.revokeObjectURL(nextUrl);
      if (!signal.aborted) timer = setTimeout(refresh, delay);
    }
  }
  await refresh();
}

function iceReady(pc, signal) {
  if (pc.iceGatheringState === 'complete' || signal.aborted) return Promise.resolve();
  return new Promise(resolve => {
    const wake = () => {
      if (pc.iceGatheringState !== 'complete' && !signal.aborted) return;
      pc.removeEventListener('icegatheringstatechange', wake);
      signal.removeEventListener('abort', wake);
      resolve();
    };
    pc.addEventListener('icegatheringstatechange', wake);
    signal.addEventListener('abort', wake, {once:true});
  });
}

async function connectCamera(ctx, view, report, signal) {
  const info = await ctx.api(webcamApi + 'info', {signal});
  if (signal.aborted) return;
  if (!info?.available) throw new Error('未发现可用的摄像头');
  const query = '?' + new URLSearchParams({cam:info.name || ''});
  const alt = info.name || '打印机摄像头';
  const snapshots = () => pollFrames(ctx, view, report, signal,
    webcamApi + 'snapshot' + query, alt, '定时快照', 2000);
  const fallback = () => {
    if (signal.aborted) return;
    if (info.snapshot_path) void snapshots();
    else report('摄像头画面暂不可用', 'error');
  };

  if (info.signal_path && typeof RTCPeerConnection === 'function') {
    let pc;
    try { pc = new RTCPeerConnection(); }
    catch { fallback(); return; }
    const stop = new AbortController();
    const video = ctx.el('video', {muted:true, autoplay:true, playsInline:true, 'aria-label':alt});
    let timeout;
    function close() {
      if (stop.signal.aborted) return;
      clearTimeout(timeout);
      stop.abort();
      pc.close();
      video.srcObject = null;
    }
    function failed() {
      if (stop.signal.aborted || signal.aborted) return;
      close();
      fallback();
    }
    signal.addEventListener('abort', close, {once:true});
    view.replaceChildren(video);
    video.addEventListener('loadeddata', () => {
      if (stop.signal.aborted || !video.videoWidth) return;
      clearTimeout(timeout);
      report('WebRTC 实时画面', 'ready');
    });
    pc.addEventListener('track', event => {
      if (!stop.signal.aborted && event.track.kind === 'video') {
        video.srcObject = event.streams[0] || new MediaStream([event.track]);
      }
    });
    pc.addEventListener('connectionstatechange', () => {
      if (['failed', 'disconnected'].includes(pc.connectionState)) failed();
    });
    timeout = setTimeout(failed, 12000);
    const post = body => ctx.api(webcamApi + 'webrtc-signal' + query, {
      method:'POST', signal:stop.signal, body
    });
    try {
      pc.addTransceiver('video', {direction:'recvonly'});
      const offer = await post({type:'request'});
      if (stop.signal.aborted) return;
      await pc.setRemoteDescription(offer);
      await pc.setLocalDescription(await pc.createAnswer());
      await iceReady(pc, stop.signal);
      if (!stop.signal.aborted) await post({
        type:pc.localDescription.type, id:offer.id, sdp:pc.localDescription.sdp
      });
    } catch {
      failed();
    }
  } else if (info.proxy_path) {
    const image = ctx.el('img', {alt});
    const timeout = setTimeout(failed, 10000);
    function stop() {
      clearTimeout(timeout);
      image.onload = image.onerror = null;
      image.removeAttribute('src');
    }
    function failed() {
      stop();
      fallback();
    }
    signal.addEventListener('abort', stop, {once:true});
    image.onload = () => {
      clearTimeout(timeout);
      if (!signal.aborted) report('MJPEG 实时画面', 'ready');
    };
    image.onerror = failed;
    view.replaceChildren(image);
    image.src = webcamApi + 'stream' + query + '&t=' + Date.now();
  } else if (info.iframe_url) {
    const url = new URL(info.iframe_url, location.href);
    if (url.origin !== location.origin || !/^\/webcam2?(\/|$)/.test(url.pathname)) {
      fallback();
      return;
    }
    const player = ctx.el('iframe', {title:alt, src:url.href, allow:'autoplay; fullscreen'});
    view.replaceChildren(player);
    report('摄像头播放器', 'ready');
  } else {
    fallback();
  }
}

export function createCameraCard(ctx, initiallyOpen) {
  return makeMedia(ctx, '摄像头', initiallyOpen, (view, report, signal) =>
    connectCamera(ctx, view, report, signal));
}

export function createMonitorMedia(ctx) {
  const openScreen = ctx.el('a', {href:'#screen'}, '打开屏幕控制');
  return ctx.el('div', {class:'monitor-media'},
    createCameraCard(ctx, true),
    makeMedia(ctx, '实体屏幕', true, (view, report, signal) =>
      pollFrames(ctx, view, report, signal, screenUrl, 'U1 实体触摸屏', '屏幕镜像', 1000), openScreen));
}
