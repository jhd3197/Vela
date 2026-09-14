import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

// Real production UI with disposable, intercepted API responses. No live
// server, installed apps, credentials or network configuration are touched.
const root = fileURLToPath(new URL('../..', import.meta.url));
const dist = path.join(root, 'web/dist');
const shots = path.join(root, 'docs/screenshots/phone-setup');
const browser = await chromium.launch({
  headless: true,
  channel: process.env.VELA_BROWSER_CHANNEL || 'chrome',
});
const errors = [];
const safari =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1';

async function fixture({
  remote = true,
  userAgent,
  standalone = false,
  blockedStorage = false,
  width = 1280,
} = {}) {
  const context = await browser.newContext({
    userAgent,
    viewport: { width, height: 844 },
    serviceWorkers: 'block',
  });
  const apiCalls = [];
  await context.addInitScript(
    ({ standalone, blockedStorage }) => {
      Object.defineProperty(navigator, 'standalone', { value: standalone });
      if (blockedStorage) {
        const getItem = Storage.prototype.getItem;
        const setItem = Storage.prototype.setItem;
        Storage.prototype.getItem = function (key) {
          if (key === 'vela.welcome.v1') throw new Error('Storage blocked');
          return getItem.call(this, key);
        };
        Storage.prototype.setItem = function (key, value) {
          if (key === 'vela.welcome.v1') throw new Error('Storage blocked');
          return setItem.call(this, key, value);
        };
      }
    },
    { standalone, blockedStorage },
  );
  await context.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (!['vela.test', 'localhost'].includes(url.hostname)) return route.abort();
    if (url.pathname.startsWith('/api/')) {
      apiCalls.push(url.pathname);
      const responses = {
        '/api/session': { token: 'disposable-fixture-token', remote },
        '/api/apps': { apps: [] },
        '/api/engine': { version: 'fixture', apps_running: 0, storage_bytes: 0 },
        '/api/settings': {},
        '/api/notifications': { notifications: [] },
        '/api/platforms': { supported: [] },
      };
      return route.fulfill({ json: responses[url.pathname] || {} });
    }
    const relative = url.pathname.replace(/^\/+/, '');
    const candidate = path.resolve(dist, relative);
    const file =
      candidate.startsWith(dist + path.sep) && path.extname(relative)
        ? candidate
        : path.join(dist, 'index.html');
    const types = {
      '.html': 'text/html',
      '.js': 'text/javascript',
      '.css': 'text/css',
      '.png': 'image/png',
      '.webmanifest': 'application/manifest+json',
    };
    try {
      await route.fulfill({
        body: await fs.readFile(file),
        contentType: types[path.extname(file)] || 'application/octet-stream',
      });
    } catch {
      await route.fulfill({ status: 404 });
    }
  });
  const page = await context.newPage();
  page.on('pageerror', (error) => errors.push(error.message));
  page.setDefaultTimeout(10000);
  return { page, context, apiCalls, base: remote ? 'https://vela.test' : 'http://localhost:7700' };
}

async function screenshot(page, name) {
  await page.screenshot({ path: path.join(shots, name + '.png'), fullPage: true });
  const overflow = await page.evaluate(() =>
    [...document.querySelectorAll('body *')]
      .filter((el) => el.getBoundingClientRect().right > innerWidth + 1)
      .map((el) => el.className)
      .slice(0, 12),
  );
  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    true,
    `No horizontal overflow at ${name}: ${JSON.stringify(overflow)}`,
  );
  const dialog = page.getByRole('dialog');
  if (await dialog.isVisible()) {
    assert.equal(
      await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth),
      true,
      'Dialog has no horizontal overflow',
    );
  }
  await page.screenshot({ path: path.join(shots, name + '.png'), fullPage: true });
}

try {
  await fs.mkdir(shots, { recursive: true });
  const local = await fixture({ remote: false });
  await local.page.goto(local.base);
  await local.page.getByRole('heading', { name: 'Welcome to Vela.' }).waitFor();
  assert.equal(await local.page.evaluate(() => document.activeElement.id), 'welcome-title');
  await screenshot(local.page, 'welcome-desktop');
  await local.page.getByRole('button', { name: 'Set up my iPhone' }).click();
  await local.page.getByRole('heading', { name: 'First, connect your phone to Vela' }).waitFor();
  assert.equal(await local.page.locator('.welcome-qr').count(), 0);
  await screenshot(local.page, 'local-access');
  await local.page.keyboard.press('Escape');
  await local.page.reload();
  await local.page.locator('.sidebar').waitFor();
  assert.equal(await local.page.getByRole('dialog').isVisible(), false);
  await local.page.goto(local.base + '/settings');
  await local.page.getByRole('link', { name: 'Set up my iPhone' }).click();
  await local.page.getByRole('heading', { name: 'First, connect your phone to Vela' }).waitFor();
  await local.page.getByRole('button', { name: 'Done for now' }).click();
  assert.equal(new URL(local.page.url()).search, '');
  await local.context.close();

  const desktop = await fixture();
  await desktop.page.goto(desktop.base + '/?setup=phone&token=do-not-share#private');
  await desktop.page.locator('.welcome-qr svg').waitFor();
  const address = await desktop.page.getByLabel('Vela setup address').inputValue();
  assert.equal(address, desktop.base + '/setup');
  await desktop.page.evaluate(() =>
    Object.defineProperty(navigator, 'clipboard', {
      value: {
        writeText: async () => {
          throw new Error('Clipboard blocked');
        },
      },
      configurable: true,
    }),
  );
  await desktop.page.getByRole('button', { name: 'Copy link', exact: true }).click();
  await desktop.page.getByText('Select and copy the address above.').waitFor();
  await screenshot(desktop.page, 'qr-desktop');
  await desktop.page.evaluate(() => (document.documentElement.dataset.theme = 'dark'));
  await screenshot(desktop.page, 'qr-desktop-dark');
  for (const width of [390, 320]) {
    await desktop.page.setViewportSize({ width, height: 740 });
    await screenshot(desktop.page, `qr-${width}`);
  }
  await desktop.context.close();

  for (const [name, userAgent] of [
    ['safari', safari],
    ['chrome-ios', safari.replace('Version/18.0', 'CriOS/129.0')],
    ['webview', safari.replace('Version/18.0 ', '')],
  ]) {
    const phone = await fixture({ userAgent, width: 390 });
    await phone.page.goto(address);
    await phone.page.getByRole('heading', { name: 'Bring Vela home.' }).waitFor();
    assert.equal(phone.apiCalls.length, 0, 'Setup instructions are available before login');
    if (name === 'safari') {
      assert.equal(await phone.page.locator('.phone-steps li').count(), 3);
      assert.equal(await phone.page.getByRole('heading', { name: 'Open in Safari' }).count(), 0);
    } else {
      await phone.page.getByRole('heading', { name: 'Open in Safari' }).waitFor();
      await phone.page.evaluate(() =>
        Object.defineProperty(navigator, 'clipboard', {
          value: {
            writeText: async () => {
              throw new Error('Blocked');
            },
          },
          configurable: true,
        }),
      );
      await phone.page.getByRole('button', { name: 'Copy link for Safari' }).click();
      await phone.page.getByText('Touch and hold the address above', { exact: false }).waitFor();
    }
    await screenshot(phone.page, name);
    if (name !== 'safari') {
      await phone.page.getByRole('button', { name: 'I’m already in Safari' }).click();
      assert.equal(await phone.page.locator('.phone-steps li').count(), 3);
    }
    await phone.page.getByRole('link', { name: 'Continue to Vela' }).click();
    await phone.page.locator('.sidebar').waitFor({ state: 'attached' });
    assert.equal(await phone.page.getByRole('dialog').isVisible(), false);
    await phone.context.close();
  }

  const installed = await fixture({ userAgent: safari, standalone: true, width: 390 });
  await installed.page.goto(installed.base);
  await installed.page.locator('.sidebar').waitFor({ state: 'attached' });
  assert.equal(await installed.page.getByRole('dialog').isVisible(), false);
  await installed.page.goto(installed.base + '/setup');
  await installed.page.getByText('Already installed', { exact: false }).waitFor();
  await installed.context.close();

  const blocked = await fixture({ blockedStorage: true });
  await blocked.page.goto(blocked.base);
  await blocked.page.getByRole('button', { name: 'Stay on this computer' }).click();
  await blocked.page.locator('.sidebar-nav').getByRole('link', { name: 'Settings' }).click();
  await blocked.page
    .locator('.sidebar-nav')
    .getByRole('link', { name: 'Home', exact: true })
    .click();
  assert.equal(await blocked.page.getByRole('dialog').isVisible(), false);
  await blocked.context.close();
  assert.deepEqual(errors, []);
  console.log(
    'Phone setup passed: first visit, dismissal/reopen, local access, HTTPS QR, clipboard fallback, Safari/Chrome/webview, standalone, blocked storage and responsive layouts.',
  );
} finally {
  await browser.close();
}
