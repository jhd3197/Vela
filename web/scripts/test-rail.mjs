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

  // An empty installation still exposes every fixed destination: three
  // shortcuts, the labelled secondary menu, and Settings.
  await page.goto(`${base}?apps=empty`);
  await page.locator('.rail').waitFor();
  assert.equal(await page.locator('.rail-apps').count(), 0);
  assert.equal(await page.locator('.rail-group a, .rail-foot button').count(), 4);
  assert.equal(await page.locator('.rail a[href="/"]').getAttribute('aria-current'), 'page');
  await page.getByRole('button', { name: 'More', exact: true }).click();
  const more = page.getByRole('menu', { name: 'More' });
  assert.deepEqual(await more.getByRole('menuitem').allInnerTexts(), [
    'Automations',
    'Manage apps',
  ]);
  // Escape closes the menu and hands focus back to the control that opened it.
  await page.keyboard.press('Escape');
  await more.waitFor({ state: 'detached' });
  assert.equal(
    await page.evaluate(() => document.activeElement.textContent.trim()),
    'More',
    'dismissing the secondary menu returns focus to its opener',
  );
  await noOverflow('empty');

  // Many apps, long names and duplicate display names keep the rail narrow and
  // in a stable, status-independent order.
  await page.goto(base);
  await page.locator('.rail-apps a').first().waitFor();
  await page.addStyleTag({
    content: '*, *::before, *::after { animation: none !important; transition: none !important; }',
  });
  const shortcuts = page.locator('.rail-apps a');
  assert.equal(await shortcuts.count(), 12);
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
  await page.locator('.appview').waitFor();
  await page.goto(base);
  await page.locator('.rail-apps a').first().waitFor();

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

  // A phone keeps the rail on screen — a ready app is one tap away, with no
  // hamburger — and the rail sits beside the content rather than over it.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(base);
  await page.locator('.rail-apps a').first().waitFor();
  await page.addStyleTag({
    content: '*, *::before, *::after { animation: none !important; transition: none !important; }',
  });
  assert.equal(await page.locator('.rail').isVisible(), true);
  assert.equal(await page.getByRole('button', { name: 'Open navigation' }).count(), 0);
  const beside = await page.evaluate(() => {
    const rail = document.querySelector('.rail').getBoundingClientRect();
    const main = document.querySelector('.workspace-main').getBoundingClientRect();
    return { railRight: rail.right, mainLeft: main.left, appsScrollable: true };
  });
  assert.ok(beside.railRight <= beside.mainLeft + 1, JSON.stringify(beside));
  await shot('home-phone-rail');
  await noOverflow('home phone');
  // One tap opens a ready app from Home.
  await page.locator('.rail-apps a[href="/app/gamma"]').click();
  await page.locator('.appview').waitFor();

  // The open app is named by the same rail and the hub's header, with no
  // second bar of its own.
  await page.locator('.app-workspace .workspace-header').waitFor();
  assert.equal(await page.getByRole('button', { name: 'Back to Apps' }).count(), 0);

  // Every other workspace keeps the same rail on screen with no drawer.
  await page.goto(base);
  await page.locator('.rail a[href="/library"]').click();
  await page.locator('.rail a[href="/library"][aria-current="page"]').waitFor();
  assert.equal(await page.locator('.rail').isVisible(), true);
  assert.equal(await page.getByRole('button', { name: 'Open navigation' }).count(), 0);
  await shot('library-phone-rail');
  assert.equal(await page.locator('.rail').getByRole('link', { name: 'Library' }).count(), 1);
  assert.equal(await page.locator('.rail').getByRole('link', { name: 'Duplicate' }).count(), 2);
  await page.locator('.rail').getByRole('link', { name: 'Gamma' }).click();
  await page.locator('.appview').waitFor();
  await noOverflow('phone');

  // An app workspace keeps the rail on screen at phone widths, with no
  // hamburger and no second rail, so switching apps stays one tap away while
  // its own panes change beneath.
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 720 });
    await page.goto(base);
    await page.locator('.rail-apps a[href="/app/workspace"]').click();
    await page.locator('.app-workspace .workspace-header').waitFor();
    assert.equal(await page.locator('.rail').count(), 1, `two rails at ${width}px`);
    assert.equal(await page.getByRole('button', { name: 'Open navigation' }).count(), 0);
    assert.equal(
      await page.locator('.rail a[href="/app/workspace"]').getAttribute('aria-current'),
      'page',
      'the open app is not marked in the rail',
    );
    // Home and Settings stay reachable from inside the app workspace.
    assert.equal(await page.locator('.rail a[href="/"]').count(), 1);
    assert.equal(await page.locator('.rail').getByRole('button', { name: 'Settings' }).count(), 1);
    // The rail takes its own column rather than covering the app.
    const geometry = await page.evaluate(() => {
      const rail = document.querySelector('.rail').getBoundingClientRect();
      const workspace = document.querySelector('.workspace').getBoundingClientRect();
      return { railWidth: rail.width, gap: workspace.left - rail.right };
    });
    assert.ok(geometry.gap >= -1, `the rail overlaps the workspace at ${width}px`);
    assert.ok(
      geometry.railWidth >= 44 && geometry.railWidth <= 70,
      `rail is ${geometry.railWidth}px at ${width}px`,
    );
    await noOverflow(`hub app at ${width}px`);
    if (width === 390) await shot('hub-app-phone-rail');

    // An app that declared no chrome is hosted the same way, reached from the
    // rail the first app is still showing.
    await page.locator('.rail-apps a[href="/app/standalone"]').click();
    await page.locator('.appview-compact.appview-hosted').waitFor();
    assert.equal(await page.locator('.rail').count(), 1, `two rails at ${width}px`);
    assert.equal(await page.locator('.workspace-header').count(), 1);
  }
  await page.setViewportSize({ width: 390, height: 720 });

  assert.deepEqual(errors, []);
  console.log(
    'PASS: empty and many-app rails, the secondary menu and its focus return, stable order, long/duplicate names, keyboard focus and selection, landmarks and named icons, 200% zoom, short-window reach, navigation without a hamburger on a phone, app workspaces keeping one rail beside them at 390/320px',
  );
} finally {
  await browser?.close();
  await server.close();
}
