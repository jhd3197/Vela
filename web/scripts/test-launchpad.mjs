import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

// The Launchpad against disposable app records in an isolated Vite fixture. It
// never contacts a Vela server or a user's installed apps. Screenshots go to
// docs/screenshots/launchpad/.
const web = fileURLToPath(new URL('..', import.meta.url));
const shots = fileURLToPath(new URL('../../docs/screenshots/launchpad', import.meta.url));
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
  const base = `http://127.0.0.1:${port}/scripts/fixtures/launchpad.html`;
  browser = await chromium.launch({
    headless: true,
    channel: process.env.VELA_BROWSER_CHANNEL || 'chrome',
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.addInitScript(() => {
    try {
      localStorage.setItem('vela.welcome.v1', 'done');
    } catch {
      // A sandboxed app frame has no same-origin storage, and needs none.
    }
  });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await fs.mkdir(shots, { recursive: true });
  const shot = async (name) => page.screenshot({ path: path.join(shots, `${name}.png`) });

  const noOverflow = async (label) => {
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    assert.ok(overflow <= 1, `${label}: horizontal overflow ${overflow}`);
  };

  await page.goto(base);
  await page.locator('.launchpad').waitFor();
  await page.locator('.launch-tile').first().waitFor();
  await shot('launchpad-1440');

  // The sections, in order, and their contents. `innerText` reflects the
  // uppercase transform the section labels carry.
  const sectionLabels = await page.locator('.launch-section-label').allInnerTexts();
  assert.deepEqual(sectionLabels, ['OPEN', 'APPS', 'VELA', 'GET MORE APPS']);

  const sectionTiles = async (label) => {
    const section = page.locator('.launch-section', { hasText: label }).first();
    return section.locator('.launch-tile .launch-label').allInnerTexts();
  };
  assert.deepEqual(await sectionTiles('Open'), ['Alpha', 'Health']);
  assert.deepEqual(await sectionTiles('Apps'), ['Alpha', 'Beta', 'Health', 'Notes', 'Zeta']);
  assert.deepEqual(await sectionTiles('Vela'), ['Ask', 'Marketplace', 'Automations', 'Settings']);

  // An app the user has not installed belongs in the Marketplace, never here.
  assert.equal(await page.getByText('Shoppe', { exact: true }).count(), 0);

  // The running dot appears on the open apps; the attention dot only on the
  // app that published `attention` in its summary. A running app is listed in
  // both Open and Apps, so its dots are counted once per tile.
  assert.ok((await page.locator('.launch-dot-running').count()) >= 2);
  assert.ok((await page.locator('.launch-dot-attention').count()) >= 1);
  // Only the app that asked for attention has the dot.
  assert.equal(
    await page
      .locator('.launch-tile', { hasText: 'Health' })
      .first()
      .locator('.launch-dot-attention')
      .count(),
    1,
  );
  assert.equal(
    await page
      .locator('.launch-tile', { hasText: 'Beta' })
      .first()
      .locator('.launch-dot-attention')
      .count(),
    0,
  );

  // --- live search --------------------------------------------------------
  const search = page.getByRole('searchbox', { name: 'Search' });
  await search.fill('bet');
  await page.waitForFunction(
    () => document.querySelectorAll('.launch-tile .launch-label').length === 1,
  );
  assert.deepEqual(await page.locator('.launch-tile .launch-label').allInnerTexts(), ['Beta']);

  await search.fill('zzznomatch');
  await page.locator('.launch-empty').waitFor();
  assert.match(await page.locator('.launch-empty').innerText(), /No app matches/);
  await search.fill('');
  await page.locator('.launch-section-label').first().waitFor();

  // Enter opens the first match.
  await search.fill('bet');
  await page.waitForFunction(
    () => document.querySelectorAll('.launch-tile .launch-label').length === 1,
  );
  await search.press('Enter');
  await page.waitForFunction(() => !document.querySelector('.launchpad'));
  assert.equal(await page.locator('.launchpad').count(), 0, 'Enter opens the app');

  // --- context menu -------------------------------------------------------
  await page.goto(base);
  await page.locator('.launch-tile').first().waitFor();
  const notes = page.locator('.launch-tile', { hasText: 'Notes' }).first();
  await notes.click({ button: 'right' });
  const menu = page.getByRole('menu');
  await menu.waitFor();
  assert.deepEqual(await menu.getByRole('menuitem').allInnerTexts(), [
    'Open',
    'Pin to rail',
    'Add widget to desk',
    'App settings',
    'Remove',
  ]);
  await shot('launchpad-menu');

  // Pinning from the Launchpad adds the app to the rail's pinned group, and the
  // menu then offers to unpin it.
  await page.getByRole('menuitem', { name: 'Pin to rail' }).click();
  await page.getByRole('menu').waitFor({ state: 'detached' });
  await page.locator('.rail-apps-group[aria-label="Pinned apps"] a[href="/app/notes"]').waitFor();
  await notes.click({ button: 'right' });
  assert.ok(
    (await page.getByRole('menu').getByRole('menuitem').allInnerTexts()).includes(
      'Unpin from rail',
    ),
    'a pinned app can be unpinned from the Launchpad',
  );
  await page.keyboard.press('Escape');
  await page.getByRole('menu').waitFor({ state: 'detached' });

  // Vela's own tools carry the Vela mark; there is one per core tile.
  assert.ok((await page.locator('.launch-vela').count()) >= 4, 'core tiles carry the Vela mark');

  // A running process app offers Stop; a widget-less app has no Add widget.
  const alpha = page.locator('.launch-tile', { hasText: 'Alpha' }).first();
  await alpha.click({ button: 'right' });
  const alphaItems = await page.getByRole('menu').getByRole('menuitem').allInnerTexts();
  assert.ok(alphaItems.includes('Stop'), 'a running process app can be stopped');
  assert.ok(!alphaItems.includes('Add widget to desk'), 'no widget, no add');
  await page.keyboard.press('Escape');
  await page.getByRole('menu').waitFor({ state: 'detached' });

  // Shift+F10 opens the menu from the keyboard and Escape returns focus.
  await notes.focus();
  await page.keyboard.press('Shift+F10');
  await page.getByRole('menu').waitFor();
  await page.keyboard.press('Escape');
  await page.getByRole('menu').waitFor({ state: 'detached' });
  assert.equal(
    await page.evaluate(() => document.activeElement?.querySelector('.launch-label')?.textContent),
    'Notes',
    'focus returns to the opener',
  );

  // --- Escape leaves ------------------------------------------------------
  await notes.focus();
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !document.querySelector('.launchpad'));
  assert.equal(await page.locator('.launchpad').count(), 0, 'Escape leaves the Launchpad');

  // --- Ctrl+Space toggles from anywhere -----------------------------------
  // Now on the Desk stub; the shortcut opens the Launchpad and toggles it back.
  await page.locator('.page-inner').waitFor();
  await page.keyboard.press('Control+Space');
  await page.locator('.launchpad').waitFor();
  await page.keyboard.press('Control+Space');
  await page.waitForFunction(() => !document.querySelector('.launchpad'));

  // --- no horizontal overflow across widths -------------------------------
  await page.keyboard.press('Control+Space');
  await page.locator('.launchpad').waitFor();
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 820 });
    await page.locator('.launch-tile').first().waitFor();
    await noOverflow(`${width}px`);
  }
  await shot('launchpad-390');

  assert.deepEqual(errors, [], `page errors: ${errors.join(', ')}`);
  console.log('test-launchpad: ok');
} finally {
  await browser?.close();
  await server.close();
}
