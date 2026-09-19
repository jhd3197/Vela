import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

// The top bar against a real engine, with three disposable apps: a fixed-size
// calculator, a weather app granted the top bar, and ordinary notes. It runs on
// a disposable data directory, so it never touches the user's own desk or
// installed apps.
//
// This is the half that cannot be checked without a browser. The focus rules,
// the geometry and the payload caps are arithmetic and are covered by
// `tests/top-bar.test.mjs`, `tests/app-windows.test.mjs` and
// `tests/bridge.test.mjs`. What is here is what only a real page can answer:
// that the bar is rendered where it should be and not where it should not,
// that clicking a window changes what it says, that a window whose app said
// "no maximize" does not have the button, that an app's published item appears
// in the rail and disappears with its window.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const python =
  process.env.VELA_TEST_PYTHON ||
  path.join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const port = 17718;
const server = spawn(python, ['scripts/serve-topbar-fixtures.py', '--port', String(port)], {
  cwd: root,
  windowsHide: true,
  stdio: ['ignore', 'pipe', 'pipe'],
});
const base = `http://127.0.0.1:${port}`;
let output = '',
  browser;
server.stdout.on('data', (data) => {
  output += data;
});
server.stderr.on('data', (data) => {
  output += data;
});
server.on('error', (error) => {
  output += error.message;
});

const shots = path.join(root, 'docs/screenshots/top-bar');

/** What the bar currently says it is looking at. */
const barName = (page) => page.locator('.topbar-name').innerText();

/** The labels of the menus the bar is offering right now. */
const menuLabels = (page) =>
  page.locator('.topbar-menu').evaluateAll((nodes) => nodes.map((node) => node.textContent.trim()));

/** The status items in the bar's right rail, as their accessible names. */
const statusItems = (page) =>
  page
    .locator('.topbar-status')
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute('aria-label')));

/** Open one of the fixture apps in a window on the desk. */
async function openApp(page, name) {
  await page.locator('.rail').getByRole('button', { name: 'All apps' }).click();
  await page.locator('.apps-overlay .launchpad').waitFor();
  await page.locator('.launch-tile', { hasText: name }).first().click();
  await page.locator(`.window-frame:has(.window-title:text-is("${name}"))`).waitFor();
  await page.waitForURL(`${base}/`);
}

const frameFor = (page, name) =>
  page.locator(`.window-frame:has(.window-title:text-is("${name}"))`);

/**
 * Run something inside a fixture app's own document.
 *
 * Looked up at the moment of the call rather than held: a window that has been
 * clicked, raised or re-rendered has a new frame, and a reference taken earlier
 * is detached by the time it is used.
 */
async function inApp(page, appId, body) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const frame = page.frames().find((entry) => entry.url().includes(appId));
    if (frame) {
      try {
        return await frame.evaluate(body);
      } catch (error) {
        if (!/detached|destroyed|Execution context/i.test(error.message)) throw error;
      }
    }
    await page.waitForTimeout(100);
  }
  throw new Error(`${appId} never offered a usable frame`);
}

try {
  for (let attempt = 0; attempt < 150; attempt += 1) {
    if (server.exitCode !== null) throw new Error(output);
    try {
      if ((await fetch(`${base}/api/health`)).ok) break;
    } catch {
      /* not up yet */
    }
    if (attempt === 149) throw new Error(`Fixture server did not start: ${output}`);
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  await fs.mkdir(shots, { recursive: true });

  browser = await chromium.launch({
    headless: true,
    ...(process.env.VELA_BROWSER_CHANNEL ? { channel: process.env.VELA_BROWSER_CHANNEL } : {}),
  });
  const context = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  await context.addInitScript(() => {
    try {
      localStorage.setItem('vela.welcome.v1', 'done');
    } catch {
      /* A sandboxed app frame has no same-origin storage, and needs none. */
    }
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));

  await page.goto(`${base}/`);
  await page.locator('.topbar').waitFor();

  // --- T01, T03: the bar names the page when nothing is open ---------------
  assert.equal(await barName(page), 'Desk', 'an empty desk is the desk');
  assert.deepEqual(await menuLabels(page), [], 'the desk has no app menus');

  for (const [route, expected] of [
    ['/library', 'Marketplace'],
    ['/ask', 'Ask'],
    ['/files', 'Files'],
    ['/automations', 'Automations'],
  ]) {
    await page.goto(base + route);
    await page.waitForFunction(
      (want) => document.querySelector('.topbar-name')?.textContent === want,
      expected,
      { timeout: 5000 },
    );
  }
  await page.goto(`${base}/`);
  await page.locator('.desk-grid').waitFor();

  // --- T04: the bar owns search and the bell, and the header does not -------
  await page.goto(`${base}/library`);
  await page.locator('.workspace-header').waitFor();
  assert.equal(
    await page
      .locator('.workspace-header .searchbox, .workspace-header .searchbox-compact')
      .count(),
    0,
    'the page header does not draw a second search field under the bar',
  );
  assert.equal(
    await page.locator('.workspace-header .notif-wrap, .workspace-header .notif-btn').count(),
    0,
    'nor a second notification bell',
  );
  await page.locator('.topbar .searchbox-compact button').click();
  await page.locator('.topbar .searchbox input').waitFor();
  await page.locator('.topbar .searchbox input').fill('weather');
  await page.locator('.search-pop .search-hit').first().waitFor();
  await page.keyboard.press('Escape');
  const bell = page
    .locator('.topbar')
    .getByRole('button', { name: /notification/i })
    .first();
  await bell.click();
  await page.locator('.notif-pop, .notif-panel').first().waitFor();
  await page.keyboard.press('Escape');

  // --- T05: the bar takes its place from the layer scale --------------------
  const layers = await page.evaluate(() => {
    const read = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return {
      declared: {
        workspace: read('--layer-workspace'),
        rail: read('--layer-rail'),
        topbar: read('--layer-topbar'),
        pop: read('--layer-pop'),
        menu: read('--layer-menu'),
      },
      barZ: getComputedStyle(document.querySelector('.topbar')).zIndex,
      railZ: getComputedStyle(document.querySelector('.rail')).zIndex,
    };
  });
  for (const [name, value] of Object.entries(layers.declared)) {
    assert.match(value, /^\d+$/, `--layer-${name} is defined in one place`);
  }
  assert.equal(layers.barZ, layers.declared.topbar, 'the bar takes its layer from the scale');
  assert.equal(layers.railZ, layers.declared.rail, 'and so does the rail');
  assert.ok(
    Number(layers.declared.menu) > Number(layers.declared.topbar),
    'a menu opens over the bar it drops out of',
  );

  // --- T02, T16: the bar follows the focused window -------------------------
  await page.goto(`${base}/`);
  await page.locator('.desk-grid').waitFor();
  await openApp(page, 'Notes');
  assert.equal(await barName(page), 'Notes');
  assert.deepEqual(await menuLabels(page), [], 'Notes was not granted the top bar');

  await openApp(page, 'Weather');
  assert.equal(await barName(page), 'Weather');
  assert.deepEqual(await menuLabels(page), ['File', 'View'], 'the focused app’s declared menus');
  await page.screenshot({ path: path.join(shots, 'weather-focused.png') });

  // Click the other window: the bar, its menus and its items all follow.
  await frameFor(page, 'Notes').locator('.window-bar').click();
  await page.waitForFunction(
    () => document.querySelector('.topbar-name')?.textContent === 'Notes',
    undefined,
    { timeout: 5000 },
  );
  assert.deepEqual(await menuLabels(page), [], 'Notes’ bar carries no menus');
  await frameFor(page, 'Weather').locator('.window-bar').click();
  await page.waitForFunction(
    () => document.querySelector('.topbar-name')?.textContent === 'Weather',
    undefined,
    { timeout: 5000 },
  );

  // --- T17: only the vetted actions are live --------------------------------
  await page.locator('.topbar-menu', { hasText: 'File' }).click();
  const menu = page.locator('.context-menu');
  await menu.waitFor();
  const items = await menu
    .locator('button')
    .evaluateAll((nodes) =>
      nodes.map((node) => ({ label: node.textContent.trim(), disabled: node.disabled })),
    );
  assert.deepEqual(
    items,
    [
      { label: 'New reading', disabled: true },
      { label: 'Back to desk', disabled: false },
      { label: 'Close window', disabled: false },
    ],
    'an item whose action this host does not perform is drawn, and inert',
  );
  await page.screenshot({ path: path.join(shots, 'menu-open.png') });
  await page.keyboard.press('Escape');
  await menu.waitFor({ state: 'detached' });

  // --- T13: the published item is in the rail, from the vetted set ----------
  await page.waitForFunction(
    () => document.querySelectorAll('.topbar-status').length === 1,
    undefined,
    {
      timeout: 15000,
    },
  );
  assert.deepEqual(
    await statusItems(page),
    ['Weather: Oslo, feels like -9'],
    'an item in somebody’s menu bar says which app put it there',
  );
  const drawn = await page
    .locator('.topbar-status')
    .first()
    .evaluate((node) => ({
      text: node.textContent.trim(),
      svgs: node.querySelectorAll('svg').length,
      images: node.querySelectorAll('img').length,
      anchors: node.querySelectorAll('a').length,
      tone: node.className,
    }));
  assert.equal(drawn.text, '-4°C');
  assert.equal(drawn.svgs, 1, 'a host icon, drawn as a host component');
  assert.equal(drawn.images, 0, 'never an image the app supplied');
  assert.equal(drawn.anchors, 0, 'never a link');
  assert.match(drawn.tone, /is-caution/, 'a named role, not a colour the app chose');

  // Publishing again replaces rather than accumulates.
  await frameFor(page, 'Weather').frameLocator('iframe').locator('#state').waitFor();
  await inApp(page, 'weather-fixture', () => window.republish());
  await page.waitForFunction(
    () => document.querySelector('.topbar-status')?.textContent.trim() === '3°C',
    undefined,
    { timeout: 5000 },
  );
  assert.equal((await statusItems(page)).length, 1, 'one item, changed, not two');

  // --- T13 (the refusals), T18: what the host will not draw -----------------
  const refusals = await inApp(page, 'weather-fixture', () => window.tryForbidden());
  for (const result of refusals) {
    assert.match(result, /^refused: /, `the host refused it: ${result}`);
  }
  assert.match(refusals[0], /icon is one of/);
  assert.match(refusals[1], /Unknown top bar item/);
  assert.match(refusals[2], /at most 12 characters/);
  assert.match(refusals[3], /At most 3 top bar items/);
  assert.equal((await statusItems(page)).length, 1, 'and drew none of them');

  // --- T12: an app without the grant cannot publish at all ------------------
  assert.equal(
    await inApp(page, 'notes-fixture', () => window.tryUngranted()),
    'refused: Top bar capability was not granted',
  );
  assert.equal((await statusItems(page)).length, 1, 'nothing of Notes’ reached the bar');

  // --- T15: clicking an item raises the window that published it ------------
  await frameFor(page, 'Notes').locator('.window-bar').click();
  await page.waitForFunction(
    () => document.querySelector('.topbar-name')?.textContent === 'Notes',
    undefined,
    { timeout: 5000 },
  );
  await page.locator('.topbar-status').first().click();
  await page.waitForFunction(
    () => document.querySelector('.topbar-name')?.textContent === 'Weather',
    undefined,
    { timeout: 5000 },
  );

  // --- T07, T08, T09: a window the shape its app says it is -----------------
  await openApp(page, 'Calc');
  assert.equal(await barName(page), 'Calc');
  const calc = frameFor(page, 'Calc');
  const geometry = await calc.evaluate((node) => ({
    width: Math.round(node.offsetWidth),
    height: Math.round(node.offsetHeight),
    left: Math.round(node.offsetLeft),
    top: Math.round(node.offsetTop),
  }));
  assert.equal(geometry.width, 320, 'the declared width is what it opened at');
  assert.equal(geometry.height, 460, 'and the declared height');
  assert.equal(await calc.locator('.window-grip').count(), 0, 'no grips on a fixed-size window');
  assert.equal(
    await calc.getByRole('button', { name: /Maximize Calc/ }).count(),
    0,
    'and no maximize button it could not honour',
  );
  assert.equal(
    await frameFor(page, 'Notes')
      .getByRole('button', { name: /Maximize Notes/ })
      .count(),
    1,
    'while an ordinary window still has one',
  );
  await page.screenshot({ path: path.join(shots, 'fixed-size-window.png') });

  // Double-clicking the title bar does nothing either.
  await calc.locator('.window-bar').dblclick();
  await page.waitForTimeout(400);
  assert.deepEqual(
    await calc.evaluate((node) => ({
      width: Math.round(node.offsetWidth),
      height: Math.round(node.offsetHeight),
    })),
    { width: 320, height: 460 },
    'a double-click on the bar does not maximize it',
  );

  // It still moves: fixed-size is not nailed down.
  const bar = await calc.locator('.window-bar').boundingBox();
  await page.mouse.move(bar.x + bar.width / 2, bar.y + bar.height / 2);
  await page.mouse.down();
  await page.mouse.move(bar.x + bar.width / 2 - 180, bar.y + bar.height / 2 + 60, { steps: 12 });
  await page.mouse.up();
  await page.waitForTimeout(400);
  const moved = await calc.evaluate((node) => ({
    width: Math.round(node.offsetWidth),
    left: Math.round(node.offsetLeft),
  }));
  assert.equal(moved.width, 320, 'moving it did not resize it');
  assert.ok(moved.left < geometry.left - 100, `it moved: ${geometry.left} → ${moved.left}`);

  // T09's other half: a place it has been put beats the declared size.
  await page.reload();
  await page.locator('.window-frame:has(.window-title:text-is("Calc"))').waitFor();
  const reopened = await frameFor(page, 'Calc').evaluate((node) => ({
    left: Math.round(node.offsetLeft),
    width: Math.round(node.offsetWidth),
  }));
  assert.equal(reopened.width, 320);
  assert.ok(
    Math.abs(reopened.left - moved.left) <= 2,
    `it came back where it was left: ${moved.left} → ${reopened.left}`,
  );

  // --- T10: the maximize glyph is a square, and restore is distinguishable --
  const notes = frameFor(page, 'Notes');
  const glyph = () =>
    notes
      .locator('.window-controls button[aria-pressed]')
      .locator('svg')
      .evaluate((node) => node.innerHTML);
  const atRest = await glyph();
  await page.screenshot({ path: path.join(shots, 'maximize-glyph.png') });
  await notes.locator('.window-controls button[aria-pressed]').click();
  await page.waitForFunction(
    () => document.querySelector('.window-controls button[aria-pressed="true"]') !== null,
    undefined,
    { timeout: 5000 },
  );
  const whenMaximized = await glyph();
  assert.notEqual(atRest, whenMaximized, 'restore is a different mark, not the same one');
  // One square to fill the desk with; two, offset, to put it back. Both are
  // single-path Phosphor marks, so what separates them is how much outline
  // each draws — the screenshots beside this are the visual record.
  assert.match(atRest, /^<path d="M/, 'the maximize mark is one drawn outline');
  assert.ok(
    whenMaximized.length > atRest.length,
    `restore draws two squares rather than one: ${atRest.length} → ${whenMaximized.length}`,
  );
  await page.screenshot({ path: path.join(shots, 'restore-glyph.png') });
  console.log('maximize glyph:', atRest);
  console.log('restore glyph:', whenMaximized);
  await notes.locator('.window-controls button[aria-pressed]').click();

  // --- T14: closing the window takes its items with it ----------------------
  //
  // First it has to come back. Maximizing another window takes every other
  // window off the desk, which unmounts them: their frames are removed, their
  // bridges close and anything they published goes down with them. That is
  // Vela's own behaviour today and not something this bar introduced — the
  // same thing happens when a window is minimized, although `WindowFrame`'s
  // own comment says minimizing must keep the app running with its session
  // intact. It is recorded as a finding in `plans/TOP-BAR-PROGRESS.md` rather
  // than fixed here, because it belongs to how the desk decides what to draw
  // and deserves its own pass across the desk suites.
  //
  // What this proves meanwhile is the half the contract does promise: an app
  // republishes from `Vela.ready`, so its items come back on their own.
  await page.waitForFunction(
    () => document.querySelectorAll('.topbar-status').length === 1,
    undefined,
    { timeout: 20000 },
  );
  assert.equal((await statusItems(page)).length, 1, 'the weather item is back, republished');
  await frameFor(page, 'Weather')
    .getByRole('button', { name: /Close Weather/ })
    .click();
  await page.waitForFunction(
    () => document.querySelectorAll('.topbar-status').length === 0,
    undefined,
    {
      timeout: 5000,
    },
  );

  // --- T03: an app's own page names the app --------------------------------
  await page.goto(`${base}/app/calc-fixture`);
  await page.waitForFunction(
    () => document.querySelector('.topbar-name')?.textContent === 'Calc',
    undefined,
    { timeout: 10000 },
  );

  // --- T01: no bar on a phone ----------------------------------------------
  const phone = await browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  await phone.addInitScript(() => {
    try {
      localStorage.setItem('vela.welcome.v1', 'done');
    } catch {
      /* not the hub page */
    }
  });
  const small = await phone.newPage();
  await small.goto(`${base}/library`);
  await small.locator('.workspace-header').waitFor();
  assert.equal(await small.locator('.topbar').count(), 0, 'no top bar at phone widths');
  assert.equal(
    await small
      .locator('.workspace-header .searchbox, .workspace-header .searchbox-compact')
      .count(),
    1,
    'so the page header carries search, exactly as it always has',
  );
  assert.ok(
    (await small
      .locator('.workspace-header')
      .getByRole('button', { name: /notification/i })
      .count()) >= 1,
    'and the bell',
  );
  await small.screenshot({ path: path.join(shots, 'phone-no-bar.png') });
  await phone.close();

  assert.deepEqual(errors, [], 'the dashboard reported no page errors');
  console.log('top bar: ok');
} finally {
  await browser?.close();
  server.kill();
}
