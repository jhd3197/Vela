import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

// Rail and workspace navigation, checked against disposable app records in an
// isolated fixture. It never contacts a Vela server or a user's installed apps.
// The rail is Desk and All apps at the top, then the apps the user pinned
// (Ask and the Library by default), a divider, the apps that are open but not
// pinned, and Settings at the foot.
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

  const groupNames = (selector) =>
    page
      .locator(`${selector} a, ${selector} button`)
      .evaluateAll((items) =>
        items.map((item) => item.querySelector('.rail-tip')?.textContent).filter(Boolean),
      );

  // An empty installation still exposes the fixed destinations and the default
  // pins: Desk and All apps above, Ask and the Library pinned, Settings
  // at the foot. No secondary "More" menu, and exactly one way to All apps —
  // the rail's own entry, which opens it over the page rather than navigating.
  await page.goto(`${base}?apps=empty`);
  await page.locator('.rail').waitFor();
  await page.locator('.rail-apps-group').first().waitFor();
  assert.deepEqual(await groupNames('.rail-group'), ['Desk', 'All apps']);
  assert.deepEqual(
    await page
      .locator('.rail-apps-group[aria-label="Pinned apps"] a')
      .evaluateAll((links) => links.map((link) => link.getAttribute('href'))),
    ['/ask', '/library'],
  );
  assert.equal(await page.locator('.rail-apps-open').count(), 0, 'nothing is open');
  assert.equal(await page.getByRole('button', { name: 'More', exact: true }).count(), 0);
  assert.equal(await page.getByRole('button', { name: 'All apps', exact: true }).count(), 1);
  assert.equal(
    await page
      .locator('.rail-group')
      .getByRole('button', { name: 'All apps', exact: true })
      .count(),
    1,
    'All apps is a rail entry, not a second drawer control',
  );
  // Settings, then the avatar for whoever this Vela belongs to.
  assert.equal(await page.locator('.rail-foot button').count(), 2);
  const avatar = page.locator('.rail-avatar-item');
  await avatar.waitFor();
  assert.equal(await avatar.locator('.rail-avatar').innerText(), 'M');
  assert.equal(
    await avatar.getAttribute('aria-label'),
    'Marco · vela.marco.house — open General settings',
    'the avatar names who and which server, not just a letter',
  );
  // It is a real control: reachable by keyboard and opening Settings.
  assert.equal(
    await avatar.evaluate((node) => {
      node.focus();
      return document.activeElement === node;
    }),
    true,
    'the avatar takes focus',
  );
  await avatar.click();
  await page.getByRole('dialog', { name: 'Settings', exact: true }).waitFor();
  await page.keyboard.press('Escape');
  await page.getByRole('dialog', { name: 'Settings', exact: true }).waitFor({ state: 'detached' });
  assert.equal(await page.locator('.rail a[href="/"]').getAttribute('aria-current'), 'page');
  await noOverflow('empty');

  // Many apps: the pinned tools stay put, and the running apps that are not
  // pinned appear under OPEN in a stable name order.
  await page.goto(base);
  await page.locator('.rail-apps a').first().waitFor();
  await page.addStyleTag({
    content: '*, *::before, *::after { animation: none !important; transition: none !important; }',
  });
  assert.deepEqual(await groupNames('.rail-apps-group[aria-label="Pinned apps"]'), [
    'Ask',
    'Marketplace',
  ]);
  const openNames = await groupNames('.rail-apps-open');
  assert.deepEqual(
    openNames,
    [...openNames].sort((a, b) => a.localeCompare(b)),
    'the OPEN group keeps its name order',
  );
  assert.deepEqual(openNames, [
    'Alpha',
    'Duplicate',
    'Gamma',
    'Standalone app',
    'Workspace app',
    'Zeta',
  ]);
  assert.equal(openNames.filter((name) => name === 'Duplicate').length, 1);
  assert.equal(
    await page.locator('.rail-apps-open .rail-section-label').innerText(),
    'OPEN',
    'the running group is labelled',
  );
  const railWidth = await page.locator('.rail').evaluate((el) => el.getBoundingClientRect().width);
  assert.equal(Math.round(railWidth), 62);
  await noOverflow('many apps');
  await shot('rail-many-light');
  await page.evaluate(() => (document.documentElement.dataset.theme = 'dark'));
  await shot('rail-many-dark');
  await page.evaluate(() => (document.documentElement.dataset.theme = 'light'));

  // --- pin, unpin and reorder from the rail's own menus -------------------
  const openTile = (name) =>
    page.locator(`.rail-apps-open a`, { has: page.locator('.rail-tip', { hasText: name }) });
  const pinnedTile = (name) =>
    page.locator(`.rail-apps-group[aria-label="Pinned apps"] a`, {
      has: page.locator('.rail-tip', { hasText: name }),
    });

  await openTile('Gamma').click({ button: 'right' });
  await page.getByRole('menuitem', { name: 'Pin to rail' }).click();
  await page.waitForFunction(() =>
    [...document.querySelectorAll('.rail-apps-group[aria-label="Pinned apps"] .rail-tip')].some(
      (tip) => tip.textContent === 'Gamma',
    ),
  );
  assert.equal(await pinnedTile('Gamma').count(), 1, 'the pinned app appears in the pinned group');
  assert.equal(await openTile('Gamma').count(), 0, 'a pinned app is no longer under OPEN');

  // Move it up, then unpin it — it returns to OPEN because it is still running.
  await pinnedTile('Gamma').click({ button: 'right' });
  await page.getByRole('menuitem', { name: 'Move up' }).click();
  await page.waitForFunction(() => {
    const names = [
      ...document.querySelectorAll('.rail-apps-group[aria-label="Pinned apps"] .rail-tip'),
    ].map((tip) => tip.textContent);
    return names.indexOf('Gamma') < names.indexOf('Marketplace');
  });
  await pinnedTile('Gamma').click({ button: 'right' });
  await page.getByRole('menuitem', { name: 'Unpin from rail' }).click();
  await page.waitForFunction(() =>
    [...document.querySelectorAll('.rail-apps-open .rail-tip')].some(
      (tip) => tip.textContent === 'Gamma',
    ),
  );
  assert.equal(await pinnedTile('Gamma').count(), 0, 'unpin removes it from the pinned group');

  // Landmarks and named controls: one nav, one main, one header, no unnamed
  // rail item.
  const landmarks = await page.evaluate(() => ({
    rail: document.querySelector('nav.rail')?.getAttribute('aria-label'),
    mains: document.querySelectorAll('main.workspace-content').length,
    headers: document.querySelectorAll('header.workspace-header').length,
    unnamed: [...document.querySelectorAll('.rail-item')].filter((item) => !item.textContent.trim())
      .length,
  }));
  assert.deepEqual(landmarks, { rail: 'Vela', mains: 1, headers: 1, unnamed: 0 });

  // Keyboard: focus a rail app, then Tab moves through the rail. Hovering
  // reveals the tooltip that names the icon.
  const first = page.locator('.rail-apps-open a').first();
  await first.hover();
  assert.equal(
    await first.locator('.rail-tip').evaluate((el) => getComputedStyle(el).opacity),
    '1',
    'hovering a rail icon reveals its name',
  );
  await first.focus();
  const before = await page.evaluate(() => document.activeElement.getAttribute('href'));
  await page.keyboard.press('Tab');
  assert.notEqual(
    before,
    await page.evaluate(() => document.activeElement.getAttribute('href')),
    'Tab moves through the rail',
  );

  // 200% browser zoom on a 1366x900 window is a 683x450 layout viewport.
  await page.setViewportSize({ width: 683, height: 450 });
  await noOverflow('200% zoom');
  const zoomed = await page.evaluate(() => {
    const last = [...document.querySelectorAll('.rail-foot button')].pop();
    return { bottom: last.getBoundingClientRect().bottom, height: innerHeight };
  });
  assert.ok(zoomed.bottom <= zoomed.height + 1, JSON.stringify(zoomed));

  // A short window keeps the utility controls reachable; the app list scrolls.
  await page.setViewportSize({ width: 1280, height: 380 });
  const reachable = await page.evaluate(() => {
    const last = [...document.querySelectorAll('.rail-foot button')].pop();
    const apps = document.querySelector('.rail-apps');
    return {
      bottom: last.getBoundingClientRect().bottom,
      height: innerHeight,
      scrollable: apps.scrollHeight > apps.clientHeight,
    };
  });
  assert.ok(reachable.bottom <= reachable.height + 1, JSON.stringify(reachable));

  // A phone keeps the rail on screen beside the content, with no hamburger, and
  // a running app is one tap away.
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
    return { railRight: rail.right, mainLeft: main.left };
  });
  assert.ok(beside.railRight <= beside.mainLeft + 1, JSON.stringify(beside));
  await shot('home-phone-rail');
  await noOverflow('home phone');
  await page.locator('.rail-apps a[href="/app/gamma"]').click();
  await page.locator('.appview').waitFor();
  await page.locator('.app-workspace .app-titlebar').waitFor();
  assert.equal(await page.getByRole('button', { name: 'Back to Apps' }).count(), 0);

  // The pinned Library selects like any other page from a phone.
  await page.goto(base);
  await page.locator('.rail a[href="/library"]').click();
  await page.locator('.rail a[href="/library"][aria-current="page"]').waitFor();
  assert.equal(await page.locator('.rail').isVisible(), true);
  await shot('library-phone-rail');

  // An app workspace keeps one rail beside it at phone widths, with no
  // hamburger and no second rail, so Desk and Settings stay one tap away.
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 720 });
    await page.goto(base);
    await page.locator('.rail-apps a[href="/app/workspace"]').click();
    await page.locator('.app-workspace .app-titlebar').waitFor();
    assert.equal(await page.locator('.rail').count(), 1, `two rails at ${width}px`);
    assert.equal(await page.getByRole('button', { name: 'Open navigation' }).count(), 0);
    assert.equal(
      await page.locator('.rail a[href="/app/workspace"]').getAttribute('aria-current'),
      'page',
    );
    assert.equal(await page.locator('.rail a[href="/"]').count(), 1);
    // Exact: the foot avatar's label mentions General settings, and a loose
    // match would count it as a second Settings control.
    assert.equal(
      await page.locator('.rail').getByRole('button', { name: 'Settings', exact: true }).count(),
      1,
    );
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

    await page.locator('.rail-apps a[href="/app/standalone"]').click();
    await page.locator('.appview-compact.appview-hosted').waitFor();
    assert.equal(await page.locator('.rail').count(), 1, `two rails at ${width}px`);
    assert.equal(await page.locator('.app-titlebar').count(), 1);
  }

  // Pin an app from its own window menu; it joins the rail's pinned group.
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(base);
  await page.locator('.rail-apps a[href="/app/workspace"]').click();
  await page.locator('.app-titlebar').waitFor();
  await page.getByRole('button', { name: 'App menu' }).click();
  await page.getByRole('menuitem', { name: 'Pin to rail' }).click();
  await page
    .locator('.rail-apps-group[aria-label="Pinned apps"] a[href="/app/workspace"]')
    .waitFor();

  // The shortcut sheet opens with ? and closes with Escape.
  await page.goto(base);
  await page.locator('.rail').waitFor();
  await page.locator('h1').first().click();
  await page.keyboard.press('Shift+Slash');
  await page.getByRole('dialog', { name: 'Keyboard shortcuts' }).waitFor();
  await page.keyboard.press('Escape');
  await page.getByRole('dialog', { name: 'Keyboard shortcuts' }).waitFor({ state: 'detached' });
  // Ctrl+1 opens the first pinned app — Ask — which then reads as selected.
  await page.keyboard.press('Control+1');
  await page.waitForFunction(
    () => document.querySelector('.rail a[href="/ask"]')?.getAttribute('aria-current') === 'page',
  );

  // With nothing running the OPEN group is absent rather than empty, and only
  // the pinned tools remain in the app region.
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.goto(`${base}?apps=idle`);
  await page.locator('.rail-apps a').first().waitFor();
  assert.equal(await page.locator('.rail-apps-open').count(), 0);
  assert.equal(await page.locator('.rail-section-label').count(), 0);
  assert.deepEqual(await groupNames('.rail-apps-group[aria-label="Pinned apps"]'), [
    'Ask',
    'Marketplace',
  ]);
  await shot('rail-idle');

  assert.deepEqual(errors, []);
  console.log(
    'PASS: default pins, pin/unpin/reorder from the rail menus, the OPEN group and its absence, no More or All apps control, stable order, landmarks and named icons, keyboard focus, 200% zoom, short-window reach, a phone rail beside content, and app workspaces keeping one rail at 390/320px',
  );
} finally {
  await browser?.close();
  await server.close();
}
