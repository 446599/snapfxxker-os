/* Simplified Chinese display layer for Paxx12 firmware-config, GPL-3.0-or-later. */
(() => {
  'use strict';
  const dictionary = __TRANSLATIONS__;
  const storageKey = 'paxx12-firmware-config-ui-language';
  const nodeStates = new WeakMap();
  const attributeStates = new WeakMap();
  const warning = '⚠️ Warning: Changing settings will restart services and may interrupt printing.';
  let language = 'zh-CN';
  try { if (localStorage.getItem(storageKey) === 'en') language = 'en'; } catch (_) {}

  function translate(source) {
    const text = source.trim();
    let translated = dictionary[text];
    if (translated === undefined && text.endsWith('\n\n' + warning)) {
      translated = translate(text.slice(0, -warning.length - 2)) + '\n\n' + dictionary[warning];
    }
    if (translated === undefined) {
      let match;
      if ((match = text.match(/^Update (.+)$/s))) translated = '更新：' + translate(match[1]);
      else if ((match = text.match(/^Change (.+) to "(.+)"\?$/s))) translated = '将“' + translate(match[1]) + '”改为“' + translate(match[2]) + '”？';
      else if ((match = text.match(/^Are you sure you want to run "(.+)"\?$/s))) translated = '确定执行“' + translate(match[1]) + '”吗？';
      else if ((match = text.match(/^(Error: |Download failed: )(.+)$/s))) translated = (match[1] === 'Error: ' ? '错误：' : '下载失败：') + translate(match[2]);
    }
    if (translated === undefined) return source;
    return source.slice(0, source.indexOf(text)) + translated + source.slice(source.indexOf(text) + text.length);
  }

  function updateText(node) {
    if (!node.parentElement || node.parentElement.closest('script,style,pre,code,textarea,[data-ui-language-toggle]')) return;
    const previous = nodeStates.get(node);
    const original = previous && node.data === previous.output ? previous.original : node.data;
    const output = language === 'zh-CN' ? translate(original) : original;
    nodeStates.set(node, { original, output });
    if (node.data !== output) node.data = output;
  }

  function updateAttributes(element) {
    if (element.closest('[data-ui-language-toggle]')) return;
    let states = attributeStates.get(element);
    if (!states) { states = {}; attributeStates.set(element, states); }
    for (const name of ['placeholder', 'title']) {
      if (!element.hasAttribute(name)) continue;
      const value = element.getAttribute(name);
      const previous = states[name];
      const original = previous && value === previous.output ? previous.original : value;
      const output = language === 'zh-CN' ? translate(original) : original;
      states[name] = { original, output };
      if (value !== output) element.setAttribute(name, output);
    }
  }

  function render() {
    const walker = document.createTreeWalker(document.documentElement, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) updateText(node);
    for (const element of document.querySelectorAll('[placeholder],[title]')) updateAttributes(element);
    document.documentElement.lang = language;
    const button = document.querySelector('[data-ui-language-toggle]');
    if (button) {
      const label = language === 'zh-CN' ? 'English' : '简体中文';
      if (button.textContent !== label) button.textContent = label;
      button.setAttribute('aria-label', language === 'zh-CN' ? '切换到英文' : 'Switch to Simplified Chinese');
    }
  }

  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'btn btn-secondary';
  button.setAttribute('data-ui-language-toggle', '');
  button.addEventListener('click', () => {
    language = language === 'zh-CN' ? 'en' : 'zh-CN';
    try { localStorage.setItem(storageKey, language); } catch (_) {}
    render();
  });
  document.querySelector('h1').appendChild(button);

  let scheduled = false;
  const observer = new MutationObserver(() => {
    if (scheduled) return;
    scheduled = true;
    queueMicrotask(() => { scheduled = false; render(); });
  });
  observer.observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['placeholder', 'title'] });
  render();
})();
