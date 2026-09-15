import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

// Built dashboard with disposable API responses; never accesses installed data.
const root = fileURLToPath(new URL('../..', import.meta.url));
const dist = path.join(root, 'web/dist');
const shots = path.join(root, 'docs/screenshots/settings');
const browser = await chromium.launch({
  headless: true,
  channel: process.env.VELA_BROWSER_CHANNEL || 'chrome',
});
const errors = [];
try {
  await fs.mkdir(shots, { recursive: true });
  const context = await browser.newContext({
    viewport: { width: 1366, height: 900 },
    serviceWorkers: 'block',
  });
  await context.addInitScript(() => localStorage.setItem('vela.welcome.v1', 'done'));
  let settings = { theme: 'light', chat_history: true, ntfy_config: {} };
  let failSave = false;
  await context.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.hostname !== 'vela.test') return route.abort();
    if (url.pathname.startsWith('/api/')) {
      if (url.pathname === '/api/settings') {
        if (route.request().method() === 'PATCH') {
          if (failSave)
            return route.fulfill({ status: 500, json: { detail: 'Fixture save failure' } });
          const patch = route.request().postDataJSON();
          settings = { ...settings, ...patch };
        }
        return route.fulfill({ json: settings });
      }
      const responses = {
        '/api/session': { token: 'fixture', remote: false },
        '/api/apps': { apps: [] },
        '/api/engine': {
          version: 'fixture',
          apps_running: 0,
          storage_bytes: 2048,
          data_dir: '/fixture/data',
        },
        '/api/health': { version: '0.1.0' },
        '/api/platforms': { current: 'windows', supported: ['windows'] },
        '/api/notifications': { notifications: [] },
        '/api/backups': { backups: [] },
        '/api/ai/status': {
          reachable: true,
          models: ['fixture-model'],
          chat_model: 'fixture-model',
        },
      };
      return route.fulfill({ json: responses[url.pathname] || {} });
    }
    const candidate = path.resolve(dist, url.pathname.replace(/^\/+/, ''));
    const file =
      candidate.startsWith(dist + path.sep) && path.extname(candidate)
        ? candidate
        : path.join(dist, 'index.html');
    const types = {
      '.html': 'text/html',
      '.js': 'text/javascript',
      '.css': 'text/css',
      '.png': 'image/png',
    };
    try {
      return route.fulfill({
        body: await fs.readFile(file),
        contentType: types[path.extname(file)] || 'application/octet-stream',
      });
    } catch {
      return route.fulfill({ status: 404 });
    }
  });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.on('pageerror', (error) => errors.push(error.message));
  const dialog = page.getByRole('dialog', { name: 'Settings', exact: true });
  await page.goto('https://vela.test/ask');
  const composer = page.locator('textarea');
  await composer.fill('Keep this unfinished question');
  const opener = page.locator('.sidebar-nav').getByRole('button', { name: 'Settings' });
  await opener.click();
  await dialog.waitFor();
  assert.equal(new URL(page.url()).pathname, '/ask');
  await dialog.getByRole('button', { name: 'Dark', exact: true }).click();
  await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
  await dialog.getByRole('button', { name: 'Done', exact: true }).waitFor();
  await page.screenshot({ path: path.join(shots, 'desktop-dark.png') });
  await dialog.getByRole('button', { name: 'Light', exact: true }).click();
  await page.waitForFunction(() => document.documentElement.dataset.theme === 'light');
  await page.screenshot({ path: path.join(shots, 'desktop-light.png') });
  await dialog.getByRole('button', { name: 'Notifications', exact: true }).click();
  await dialog.getByLabel('Topic', { exact: true }).fill('unsaved-fixture-topic');
  await dialog.getByRole('button', { name: 'Storage', exact: true }).click();
  await dialog.getByRole('button', { name: 'Notifications', exact: true }).click();
  assert.equal(
    await dialog.getByLabel('Topic', { exact: true }).inputValue(),
    'unsaved-fixture-topic',
  );
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await dialog.getByText('Notification settings saved.', { exact: true }).waitFor();
  assert.equal(settings.ntfy_config.topic, 'unsaved-fixture-topic');
  await dialog.getByRole('button', { name: 'Chat & privacy' }).click();
  await dialog.getByRole('button', { name: 'Off', exact: true }).click();
  await page.waitForFunction(() => !localStorage.getItem('vela-chat'));
  await dialog.getByRole('button', { name: 'Done', exact: true }).click();
  assert.equal(settings.chat_history, false);
  assert.equal(await composer.inputValue(), 'Keep this unfinished question');
  assert.equal(await opener.evaluate((el) => el === document.activeElement), true);
  await opener.click();
  const search = dialog.getByRole('searchbox', { name: 'Find a setting' });
  await search.fill('ollama');
  assert.equal(await dialog.getByRole('navigation').getByRole('button').count(), 1);
  await search.fill('nothingmatches');
  await dialog.getByText('No settings found.').waitFor();
  await search.fill('');
  for (let i = 0; i < 17; i++) {
    await page.keyboard.press('Tab');
    // Native dialogs allow a tab stop in browser chrome (body is then active),
    // but never allow focus into the inert dashboard beneath the popup.
    assert.equal(
      await dialog.evaluate(
        (el) => el.contains(document.activeElement) || document.activeElement === document.body,
      ),
      true,
    );
  }
  failSave = true;
  await dialog.getByRole('button', { name: 'Dark', exact: true }).click();
  await dialog.getByRole('alert').getByText('Fixture save failure').waitFor();
  assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
  failSave = false;
  await page.keyboard.press('Escape');
  await dialog.waitFor({ state: 'detached' });
  await opener.click();
  await page.mouse.click(5, 5);
  await dialog.waitFor({ state: 'detached' });

  // Old bookmarks, search shortcuts, small screens, and both themes.
  await page.goto('https://vela.test/settings#backups');
  await dialog.getByRole('button', { name: 'Create backup' }).waitFor();
  await dialog.getByRole('button', { name: 'Done', exact: true }).click();
  assert.equal(new URL(page.url()).pathname, '/');
  await page.getByRole('searchbox', { name: 'Search', exact: true }).fill('storage');
  await page.getByRole('button', { name: 'Storage Settings' }).click();
  await dialog.getByText('/fixture/data', { exact: true }).waitFor();
  await dialog.getByRole('button', { name: 'Done', exact: true }).click();
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 700 });
    await page.locator('.tabbar').getByRole('button', { name: 'Settings' }).click();
    for (const theme of ['light', 'dark']) {
      await dialog
        .getByRole('button', { name: theme === 'dark' ? 'Dark' : 'Light', exact: true })
        .click();
      await page.waitForFunction((t) => document.documentElement.dataset.theme === t, theme);
      for (const section of ['Notifications', 'General', 'Appearance']) {
        await dialog
          .getByRole('navigation')
          .getByRole('button', { name: section, exact: true })
          .click();
        const fits = await dialog.evaluate((el) => {
          const bounds = el.getBoundingClientRect();
          const content = el.querySelector('.settings-content');
          return (
            bounds.left >= 0 &&
            bounds.right <= innerWidth &&
            bounds.bottom <= innerHeight &&
            content.scrollWidth <= content.clientWidth
          );
        });
        assert.ok(fits, `${section} overflows at ${width}px`);
      }
      await page.screenshot({ path: path.join(shots, `phone-${width}-${theme}.png`) });
    }
    await dialog.getByRole('button', { name: 'Done', exact: true }).click();
  }
  assert.deepEqual(errors, []);
  console.log(
    'PASS: settings popup, page/draft preservation, saves and rollback, category search, focus containment/restoration, Escape/backdrop, deep links and phone layouts',
  );
} finally {
  await browser.close();
}
