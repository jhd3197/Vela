import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

// Rail and workspace navigation, checked against disposable app records in an
// isolated fixture. It never contacts a Vela server or a user's installed apps.
const web = fileURLToPath(new URL('..', import.meta.url));
const shots = fileURLToPath(new URL('../../docs/screenshots/rail', import.meta.url));
const server = await createServer({
  configFile: false,
  root: web,
  plugins: [react()],
  css: { preprocessorOptions: { scss: { api: 'modern' } } },
  server: { host: '127.0.0.1', port: 0 },
});
let browser;
try {
  await server.listen();
  const { port } = server.httpServer.address();
  const base = `http://127.0.0.1:${port}/scripts/fixtures/rail.html`;
  browser = await chromium.launch({
    headless: true,
    channel: process.env.VELA_BROWSER_CHANNEL || 'chrome',
  });
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  await page.addInitScript(() => localStorage.setItem('vela.welcome.v1', 'done'));
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await fs.mkdir(shots, { recursive: true });
  const shot = async (name) => page.screenshot({ path: path.join(shots, `${name}.png`) });

  const noOverflow = async (label) => {
    const overflow = await page.evaluate(() => {
      const content = document.querySelector('.workspace-content');
      return {
        body: document.documentElement.scrollWidth - innerWidth,
        content: content ? content.scrollWidth - content.clientWidth : 0,
      };
    });
    assert.ok(overflow.body <= 1 && overflow.content <= 1, `${label}: ${JSON.stringify(overflow)}`);
  };

  // An empty installation still exposes every fixed destination.
  await page.goto(`${base}?apps=empty`);
  await page.locator('.rail').waitFor();
  assert.equal(await page.locator('.rail-apps').count(), 0);
  assert.equal(await page.locator('.rail-group a, .rail-foot button').count(), 7);
  assert.equal(await page.locator('.rail a[href="/"]').getAttribute('aria-current'), 'page');
  await noOverflow('empty');

  // Many apps, long names and duplicate display names keep the rail narrow and
  // in a stable, status-independent order.
  await page.goto(base);
  await page.locator('.rail-apps a').first().waitFor();
  await page.addStyleTag({
    content: '*, *::before, *::after { animation: none !important; transition: none !important; }',
  });
  const shortcuts = page.locator('.rail-apps a');
  assert.equal(await shortcuts.count(), 10);
  const names = await shortcuts.evaluateAll((items) =>
    items.map((item) => item.querySelector('.rail-tip').textContent),
  );
  assert.deepEqual(
    names,
    [...names].sort((a, b) => a.localeCompare(b)),
  );
  assert.equal(names.filter((name) => name === 'Duplicate').length, 2);
  const railWidth = await page.locator('.rail').evaluate((el) => el.getBoundingClientRect().width);
  assert.equal(Math.round(railWidth), 62);
  await noOverflow('many apps');
  await shot('rail-many-light');
  await page.evaluate(() => (document.documentElement.dataset.theme = 'dark'));
  await shot('rail-many-dark');
  await page.evaluate(() => (document.documentElement.dataset.theme = 'light'));

  // Every shortcut has an accessible name, and keyboard focus reveals it.
  const long = page.locator('.rail-apps a').nth(0);
  assert.equal(
    await long.getAttribute('href'),
    '/app/long',
    'the alphabetically first app leads the list',
  );
  await long.focus();
  assert.equal(await long.locator('.rail-tip').evaluate((el) => getComputedStyle(el).opacity), '1');
  const beforeTab = await page.evaluate(() => document.activeElement.getAttribute('href'));
  await page.keyboard.press('Tab');
  const afterTab = await page.evaluate(() => document.activeElement.getAttribute('href'));
  assert.notEqual(beforeTab, afterTab, 'Tab moves through the rail');
  await page.keyboard.press('Enter');
  await page.getByRole('heading', { name: 'App workspace' }).waitFor();
  assert.equal(
    await page.locator('.rail-apps a[aria-current="page"]').getAttribute('href'),
    afterTab,
    'the open app is the selected shortcut',
  );

  // Landmarks: one navigation for the rail, one main surface, one header.
  const landmarks = await page.evaluate(() => ({
    rail: document.querySelector('nav.rail')?.getAttribute('aria-label'),
    mains: document.querySelectorAll('main.workspace-content').length,
    headers: document.querySelectorAll('header.workspace-header').length,
    unnamedIcons: [...document.querySelectorAll('.rail-item')].filter(
      (item) => !item.textContent.trim(),
    ).length,
  }));
  assert.deepEqual(landmarks, { rail: 'Vela', mains: 1, headers: 1, unnamedIcons: 0 });

  // 200% browser zoom on a 1366x900 window is a 683x450 layout viewport.
  await page.setViewportSize({ width: 683, height: 450 });
  await noOverflow('200% zoom');
  const zoomed = await page.evaluate(() => {
    const settings = [...document.querySelectorAll('.rail-foot button')].pop();
    return { bottom: settings.getBoundingClientRect().bottom, height: innerHeight };
  });
  assert.ok(zoomed.bottom <= zoomed.height + 1, JSON.stringify(zoomed));

  // A short window keeps the utility controls reachable; the app list scrolls.
  await page.setViewportSize({ width: 1280, height: 420 });
  const reachable = await page.evaluate(() => {
    const settings = [...document.querySelectorAll('.rail-foot button')].pop();
    const box = settings.getBoundingClientRect();
    const apps = document.querySelector('.rail-apps');
    return {
      bottom: box.bottom,
      height: innerHeight,
      scrollable: apps.scrollHeight > apps.clientHeight,
    };
  });
  assert.ok(reachable.bottom <= reachable.height + 1, JSON.stringify(reachable));
  assert.ok(reachable.scrollable, 'the app shortcuts scroll in a short window');

  // Phones swap the rail for a labelled drawer with the same destinations.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(base);
  await page.locator('.tabbar').waitFor();
  await page.addStyleTag({
    content: '*, *::before, *::after { animation: none !important; transition: none !important; }',
  });
  assert.equal(await page.locator('.rail').isVisible(), false);
  const opener = page.getByRole('button', { name: 'Open navigation' });
  await opener.click();
  const drawer = page.getByRole('dialog', { name: 'Vela navigation' });
  await drawer.waitFor();
  await shot('nav-drawer-phone');
  assert.equal(await drawer.getByRole('link', { name: 'Library' }).count(), 1);
  assert.equal(await drawer.getByRole('link', { name: 'Duplicate' }).count(), 2);
  await page.keyboard.press('Escape');
  await drawer.waitFor({ state: 'detached' });
  assert.equal(
    await page.evaluate(() => document.activeElement.getAttribute('aria-label')),
    'Open navigation',
    'dismissing the drawer returns focus to its opener',
  );
  await opener.click();
  await drawer.getByRole('link', { name: 'Gamma' }).click();
  await drawer.waitFor({ state: 'detached' });
  await page.getByRole('heading', { name: 'App workspace' }).waitFor();
  await noOverflow('phone');

  assert.deepEqual(errors, []);
  console.log(
    'PASS: empty and many-app rails, stable order, long/duplicate names, keyboard focus and selection, landmarks and named icons, 200% zoom, short-window reach, phone drawer navigation and focus return',
  );
} finally {
  await browser?.close();
  await server.close();
}
