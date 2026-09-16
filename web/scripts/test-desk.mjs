import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

// The desk against a real engine: adding, moving, resizing, undoing, saving,
// and finding the same arrangement after a reload. It runs on a disposable
// data directory, so it never touches the user's own desk or installed apps.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const python =
  process.env.VELA_TEST_PYTHON ||
  path.join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const server = spawn(python, ['scripts/serve-release-fixtures.py'], {
  cwd: root,
  windowsHide: true,
  stdio: ['ignore', 'pipe', 'pipe'],
});
const base = 'http://127.0.0.1:17715';
let output = '',
  browser;
server.stderr.on('data', (data) => {
  output += data;
});
server.on('error', (error) => {
  output += error.message;
});

const geometry = (page) =>
  page.evaluate(() =>
    [...document.querySelectorAll('.desk-frame')].map((frame) => ({
      label: frame.getAttribute('aria-label'),
      left: Math.round(frame.offsetLeft),
      top: Math.round(frame.offsetTop),
      width: Math.round(frame.offsetWidth),
      height: Math.round(frame.offsetHeight),
    })),
  );

const labels = async (page) => (await geometry(page)).map((frame) => frame.label);

try {
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw new Error(output);
    try {
      if ((await fetch(base + '/api/health')).ok) break;
    } catch {}
    if (i === 99) throw new Error(`Fixture server did not start: ${output}`);
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  browser = await chromium.launch({
    headless: true,
    channel: process.env.VELA_BROWSER_CHANNEL || 'chrome',
  });
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  await page.addInitScript(() => localStorage.setItem('vela.welcome.v1', 'done'));
  // The board settles into its new geometry over 160ms. Measuring mid-flight
  // would make every assertion here a race, so the transition is switched off:
  // the settle animation has its own `prefers-reduced-motion` rule and is not
  // what this suite is about.
  await page.addInitScript(() => {
    addEventListener('DOMContentLoaded', () => {
      const style = document.createElement('style');
      style.textContent =
        '*, *::before, *::after { animation: none !important; transition: none !important; }';
      document.head.append(style);
    });
  });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));

  const arrange = page.getByRole('button', { name: 'Arrange desk', exact: true });
  const add = page.getByRole('button', { name: 'Add widget', exact: true });
  const done = page.getByRole('button', { name: 'Done', exact: true });

  await page.goto(base + '/');
  await page.locator('.desk-grid').waitFor();
  assert.deepEqual(await labels(page), ['Clock', 'Your apps', 'Running now', 'Ask']);

  // --- add a widget -------------------------------------------------------
  await add.click();
  const library = page.getByRole('dialog', { name: 'Add a widget', exact: true });
  await library.waitFor();
  // A widget only appears here because Vela can actually answer for it.
  await library.getByRole('heading', { name: 'Vela', exact: true }).waitFor();
  await library.getByRole('searchbox', { name: 'Find a widget' }).fill('no-such-widget');
  await library.getByText('No widgets match').waitFor();
  await library.getByRole('searchbox', { name: 'Find a widget' }).fill('system');
  await library.getByRole('button', { name: /^System/ }).click();
  await library.waitFor({ state: 'detached' });
  // Choosing one puts the desk straight into Arrange mode with it selected.
  await done.waitFor();
  assert.ok((await labels(page)).includes('System'), await labels(page));

  // --- move and resize with the keyboard ----------------------------------
  const system = page.getByRole('region', { name: 'System', exact: true });
  await system.focus();
  const before = (await geometry(page)).find((frame) => frame.label === 'System');
  await page.keyboard.press('ArrowRight');
  const moved = (await geometry(page)).find((frame) => frame.label === 'System');
  assert.ok(moved.left > before.left, `arrow key must move the widget: ${JSON.stringify(moved)}`);
  await page.keyboard.press('Shift+ArrowDown');
  const grown = (await geometry(page)).find((frame) => frame.label === 'System');
  assert.ok(grown.height > moved.height, `Shift+arrow must resize: ${JSON.stringify(grown)}`);
  const announced = await page.locator('[role="status"][aria-live="polite"]').innerText();
  assert.ok(announced.trim().length > 0, 'every arrangement change is announced');

  // Undo takes the resize back, and redo puts it on again.
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  assert.equal(
    (await geometry(page)).find((frame) => frame.label === 'System').height,
    moved.height,
  );
  await page.getByRole('button', { name: 'Redo', exact: true }).click();
  assert.equal(
    (await geometry(page)).find((frame) => frame.label === 'System').height,
    grown.height,
  );

  // --- drag with the pointer ----------------------------------------------
  // The board has gravity: a widget dragged into empty space below floats
  // straight back up, which is the engine working, not a failed drag. So the
  // drag that is checked here is sideways, where the column really changes.
  const head = system.locator('.desk-frame-head');
  const box = await head.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 - 400, box.y + box.height / 2, { steps: 12 });
  await page.mouse.up();
  const dragged = (await geometry(page)).find((frame) => frame.label === 'System');
  assert.ok(dragged.left < grown.left, `dragging must move the widget: ${JSON.stringify(dragged)}`);

  // --- save, reload, and find it there ------------------------------------
  await done.click();
  await arrange.waitFor();
  await page.reload();
  await page.locator('.desk-grid').waitFor();
  const kept = (await geometry(page)).find((frame) => frame.label === 'System');
  assert.ok(kept, `the saved widget survived a reload: ${JSON.stringify(await labels(page))}`);
  assert.deepEqual(
    { left: kept.left, top: kept.top, height: kept.height },
    { left: dragged.left, top: dragged.top, height: dragged.height },
    'the saved arrangement is the one that comes back',
  );

  // The System widget draws the real endpoint: a host line and a memory meter.
  await system.getByRole('progressbar', { name: 'Memory' }).waitFor();
  assert.match(await system.innerText(), /up \d+|Collecting|cannot read its own CPU/);

  // --- cancel puts an edit back -------------------------------------------
  await arrange.click();
  await done.waitFor();
  await system.focus();
  await page.keyboard.press('ArrowRight');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await arrange.waitFor();
  assert.equal((await geometry(page)).find((frame) => frame.label === 'System').left, kept.left);

  // --- the leave guard ----------------------------------------------------
  await arrange.click();
  await system.focus();
  await page.keyboard.press('ArrowRight');
  await page.locator('.rail a[href="/library"]').click();
  const guard = page.getByRole('dialog', { name: 'Keep your changes to the desk?' });
  await guard.waitFor();
  await guard.getByRole('button', { name: 'Cancel', exact: true }).click();
  await guard.waitFor({ state: 'detached' });
  assert.equal(new URL(page.url()).pathname, '/', 'Cancel stays on the desk');
  await page.locator('.rail a[href="/library"]').click();
  await guard.waitFor();
  await guard.getByRole('button', { name: 'Discard', exact: true }).click();
  await page.waitForURL(`${base}/library`);

  // --- remove it again ----------------------------------------------------
  await page.goto(base + '/');
  await arrange.click();
  await page.getByRole('button', { name: 'Menu for System', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Remove', exact: true }).click();
  await done.click();
  await arrange.waitFor();
  await page.reload();
  await page.locator('.desk-grid').waitFor();
  assert.ok(!(await labels(page)).includes('System'), await labels(page));

  // --- the phone board is its own board -----------------------------------
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(base + '/');
  await page.locator('.desk-grid').waitFor();
  assert.deepEqual(
    await labels(page),
    ['Clock', 'Your apps', 'Ask'],
    'arranging the desktop board must not reflow the phone board',
  );

  // A long press is how a phone reaches Arrange mode.
  const clock = page.getByRole('region', { name: 'Clock', exact: true });
  const clockBox = await clock.boundingBox();
  await page.touchscreen.tap(1, 1).catch(() => {});
  await page.evaluate(
    ({ x, y }) => {
      const frame = document.querySelector('.desk-frame');
      const event = (type) =>
        frame.dispatchEvent(
          new PointerEvent(type, {
            bubbles: true,
            pointerId: 1,
            pointerType: 'touch',
            clientX: x,
            clientY: y,
          }),
        );
      event('pointerdown');
    },
    { x: clockBox.x + 20, y: clockBox.y + 20 },
  );
  await page.getByRole('button', { name: 'Done', exact: true }).waitFor({ timeout: 4000 });
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();

  // Nothing overflows sideways while arranging at the narrowest supported width.
  for (const size of [
    { width: 320, height: 720 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(size);
    await page.goto(base + '/');
    await page.locator('.desk-grid').waitFor();
    await page.getByRole('button', { name: 'Arrange desk', exact: true }).click();
    await page.getByRole('button', { name: 'Done', exact: true }).waitFor();
    const overflow = await page.evaluate(() => {
      const content = document.querySelector('.workspace-content');
      const host = document.querySelector('.desk-grid');
      return {
        body: document.documentElement.scrollWidth - innerWidth,
        content: content.scrollWidth - content.clientWidth,
        grid: host.scrollWidth - host.clientWidth,
      };
    });
    assert.ok(
      overflow.body <= 1 && overflow.content <= 1 && overflow.grid <= 1,
      `arrange mode at ${size.width}px: ${JSON.stringify(overflow)}`,
    );
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  }

  assert.deepEqual(errors, []);
  console.log(
    'PASS: the desk adds, moves, resizes, undoes, redoes, saves and reloads a widget; keyboard ' +
      'arrangement is announced; Cancel restores; leaving with unsaved changes asks; removing ' +
      'persists; the phone board stays its own; long-press arranges; no overflow at 320/390',
  );
} finally {
  await browser?.close();
  server.kill();
  if (server.exitCode === null) await new Promise((resolve) => server.once('exit', resolve));
}
