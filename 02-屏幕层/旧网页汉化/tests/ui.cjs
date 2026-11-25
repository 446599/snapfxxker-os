// Isolated local-page tests. Every network request is mocked; no printer mutations.
const { chromium } = require(process.env.PAXX_UI_PLAYWRIGHT || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const read = name => JSON.parse(fs.readFileSync(path.join(root, 'original', `${name}.json`), 'utf8'));
const status = {
  version: { title: 'System Information', items: [
    { label: 'Base', value: '2.0.0.205' }, { label: 'Version', value: '2.0.0-paxx12-23' },
    { label: 'Profile', value: 'extended' }, { label: 'Active Firmware', value: 'A' }
  ] }
};
const responses = { settings: read('settings'), actions: read('actions'), 'quick-actions': {}, links: read('links'), status };
const select = (page, id) => page.locator(`select[onchange^="updateSetting('${id}',"]`);
const errors = [];
const mutations = [];

(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 960 } });
    await context.route('**/*', async route => {
      const request = route.request();
      if (request.method() !== 'GET') { mutations.push(request.method() + ' ' + request.url()); return route.abort(); }
      const url = new URL(request.url());
      if (url.pathname === '/firmware-config/') return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(path.join(root, 'dist/index.html'), 'utf8') });
      const key = url.pathname.split('/api/')[1];
      if (responses[key]) return route.fulfill({ contentType: 'application/json', body: JSON.stringify(responses[key]) });
      return route.abort();
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://ui-test.local/firmware-config/');
    await page.getByText('快造组件', { exact: true }).waitFor();
    assert.equal(await page.title(), '固件设置');
    assert.equal(await select(page, 'rfid').inputValue(), 'openrfid-generic');
    const expected = Object.values(responses.settings).flatMap(group => group.items);
    for (const item of expected) {
      const actual = await select(page, item.id).evaluate(el => ({ value: el.value, options: [...el.options].map(option => option.value) }));
      assert.equal(actual.value, item.current, item.id);
      assert.deepEqual(actual.options, Object.entries(item.options).filter(([key, value]) => !value.hidden || key === item.current).map(([key]) => key), item.id);
    }
    await select(page, 'rfid').selectOption('snapmaker');
    await page.getByText('更新：耗材标签识别', { exact: true }).waitFor();
    assert.match(await page.locator('#confirm-message').innerText(), /可能中断打印/);
    await page.locator('#confirm-modal').getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(await select(page, 'rfid').inputValue(), 'openrfid-generic');

    await select(page, 'max_speed').selectOption('aggressive');
    await page.getByText('更新：运动与换头提速', { exact: true }).waitFor();
    const warning = await page.locator('#confirm-message').innerText();
    for (const term of ['750 mm/s', '700 mm/s', '50 mm/s', '磨损', '错层', '振纹', '校准']) assert.ok(warning.includes(term), term);
    await page.locator('#confirm-modal').getByRole('button', { name: '取消', exact: true }).click();

    await select(page, 'chamber_heater').selectOption('auto');
    await page.getByText('更新：PandaBreath / DragonBreath 腔体加热器', { exact: true }).waitFor();
    assert.equal(await page.locator('#confirm-btn').isDisabled(), true);
    await page.locator('#cfi-ACKNOWLEDGED').fill('我已理解');
    await page.locator('#cfi-PANDA_BREATH_IP').fill('192.168.1.100');
    assert.equal(await page.locator('#confirm-btn').isDisabled(), true);
    await page.locator('#cfi-ACKNOWLEDGED').fill('I UNDERSTAND');
    assert.equal(await page.locator('#confirm-btn').isEnabled(), true);
    const inputs = await page.evaluate(() => getConfirmInputValues());
    assert.deepEqual(inputs, { ACKNOWLEDGED: 'I UNDERSTAND', PANDA_BREATH_IP: '192.168.1.100' });
    await page.locator('#confirm-modal').getByRole('button', { name: '取消', exact: true }).click();

    await page.locator('[data-ui-language-toggle]').click();
    await page.getByText('Snapmaker Components', { exact: true }).waitFor();
    assert.equal(await page.title(), 'Firmware Config');
    await page.reload();
    await page.getByText('Snapmaker Components', { exact: true }).waitFor();
    assert.equal(await page.title(), 'Firmware Config');
    await page.locator('[data-ui-language-toggle]').click();
    await page.getByText('快造组件', { exact: true }).waitFor();
    await page.getByText('快造组件', { exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(root, 'dist/preview-desktop.png') });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByText('快造组件', { exact: true }).scrollIntoViewIfNeeded();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'mobile overflow');
    await page.screenshot({ path: path.join(root, 'dist/preview-mobile.png') });
    // Newly added warning nodes and text replacements must retain both languages.
    await page.evaluate(() => { document.querySelector('#action-message').textContent = 'Completed successfully'; });
    await page.waitForFunction(() => document.querySelector('#action-message').textContent === '操作成功');
    assert.deepEqual(errors, [], 'browser errors');
    assert.deepEqual(mutations, [], 'unexpected mutations');
    fs.writeFileSync(path.join(root, 'dist/test-results.json'), JSON.stringify({ result: 'pass', settings_checked: expected.length, api_values_preserved: true, cancellation_preserved: true, warning_and_input_validation_preserved: true, english_toggle_persists: true, mobile_overflow: false, printer_mutations: 0, browser_errors: errors }, null, 2));
    console.log('PASS: all option values, cancelled changes, warning text, input validation, bilingual persistence, mobile layout; zero POST requests.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
