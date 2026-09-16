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
  // Init scripts run in every frame, including the sandboxed app view, where
  // localStorage is deliberately unreachable. Only the hub page needs the flag.
  await page.addInitScript(() => {
    try {
      localStorage.setItem('vela.welcome.v1', 'done');
    } catch {
      // A sandboxed app frame has no same-origin storage, and needs none.
    }
  });
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

  const done = page.getByRole('button', { name: 'Done', exact: true });
  // Arrange desk, Add widget and Personalise moved off the search row into the
  // top-right "Desk options" ⋯ menu (and the wallpaper right-click). In Arrange
  // mode, Add widget is a toolbar button. These helpers reach whichever is live.
  const deskOptions = page.getByRole('button', { name: 'Desk options', exact: true });
  const menuItem = (name) => page.getByRole('menuitem', { name, exact: true });
  const arrange = {
    click: async () => {
      await deskOptions.click();
      await menuItem('Arrange desk').click();
    },
    waitFor: () => deskOptions.waitFor(),
  };
  const add = {
    click: async () => {
      const toolbar = page.getByRole('button', { name: 'Add widget', exact: true });
      if (await toolbar.count()) await toolbar.click();
      else {
        await deskOptions.click();
        await menuItem('Add widget').click();
      }
    },
  };

  await page.goto(base + '/');
  await page.locator('.desk-grid').waitFor();
  assert.deepEqual(await labels(page), ['Clock', 'Your apps', 'Running now', 'Needs you', 'Ask']);

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

  // --- the health widget --------------------------------------------------
  // It reports the engine's last sweep and never starts one by being looked
  // at; running the checks is something the person asks for.
  await page.goto(base + '/');
  await add.click();
  await library.waitFor();
  await library.getByRole('searchbox', { name: 'Find a widget' }).fill('health');
  await library.getByRole('button', { name: /^Health/ }).click();
  await library.waitFor({ state: 'detached' });
  await done.click();
  await arrange.waitFor();
  const health = page.getByRole('region', { name: 'Health', exact: true });
  await health.getByText('Vela has not checked itself yet.').waitFor();

  await health.getByRole('button', { name: 'Run checks' }).click();
  // A real engine answers here, so the widget shows whatever this disposable
  // server actually reports rather than a canned result.
  await health.locator('.desk-stat-value').waitFor();
  const verdict = await health.locator('.desk-stat-value').innerText();
  assert.ok(
    /All good|to look at/.test(verdict),
    `the health widget must report the sweep: ${verdict}`,
  );
  // Whatever it found, it offers the way to the section that can act on it.
  await health.getByRole('link', { name: /Health|Fix it/ }).waitFor();

  await arrange.click();
  await page.getByRole('button', { name: 'Menu for Health', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Remove', exact: true }).click();
  await done.click();
  await arrange.waitFor();

  // --- the phone board is its own board -----------------------------------
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(base + '/');
  await page.locator('.desk-grid').waitFor();
  assert.deepEqual(
    await labels(page),
    ['Clock', 'Needs you', 'Your apps', 'Ask'],
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
    await arrange.click();
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

  // --- a widget an app provides -------------------------------------------
  // The fixture app declares two widgets and publishes a summary for each
  // through the real bridge operation, so this covers the whole contract:
  // manifest declaration, install review, publish, and host rendering.
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.goto(base + '/library');
  const installed = await page.evaluate(
    async (folder) => {
      const session = await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } });
      const { token } = await session.json();
      const hub = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
      const review = await (
        await fetch('/api/releases/prepare', {
          method: 'POST',
          headers: hub,
          body: JSON.stringify({ folder }),
        })
      ).json();
      if (!review.review) return { error: review.detail || 'prepare failed' };
      const committed = await fetch(`/api/releases/${review.review}/commit`, {
        method: 'POST',
        headers: hub,
        body: JSON.stringify({
          capabilities: review.capabilities,
          operations: review.operations,
        }),
      });
      return {
        capabilities: review.capabilities,
        widgets: review.widgets,
        ok: committed.ok,
      };
    },
    path.join(root, 'tests/fixtures/widget-fixture'),
  );
  assert.ok(installed.ok, `installing the widget fixture: ${JSON.stringify(installed)}`);
  // The review names the widgets the app wants to put on the desk.
  assert.ok(installed.capabilities.includes('widgets'), JSON.stringify(installed));
  assert.deepEqual(
    installed.widgets.map((widget) => widget.id),
    ['sync', 'queued'],
  );

  // Opening the app runs its publish through the bridge.
  await page.goto(base + '/app/widget-fixture');
  await page.locator('.appview').waitFor();
  for (let i = 0; i < 100; i++) {
    const published = await page.evaluate(async () => {
      const session = await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } });
      const { token } = await session.json();
      const response = await fetch('/api/apps/widget-fixture/widgets', {
        headers: { Authorization: `Bearer ${token}` },
      });
      return response.json();
    });
    if (published.widgets?.[0]?.summary) break;
    if (i === 99) throw new Error('the fixture app never published a summary');
    await new Promise((resolve) => setTimeout(resolve, 100));
  }

  // Dragging an app tile off the desk's own Apps widget and onto the board makes
  // that app's widget where it lands, and opens Arrange so it can be moved
  // straight away. Cancel puts the board back, leaving the flow below on the
  // arrangement it expects.
  await page.goto(base + '/');
  await page.locator('.desk-grid').waitFor();
  const appTile = '.desk-tiles .tile-card:has-text("Widget Fixture")';
  await page.locator(appTile).first().waitFor();
  await page.dragAndDrop(appTile, '.desk-grid', { targetPosition: { x: 40, y: 40 } });
  await page.getByRole('region', { name: 'Sync', exact: true }).waitFor();
  assert.ok(await done.count(), 'dropping an app onto the board opens Arrange mode');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await arrange.waitFor();
  await page.getByRole('region', { name: 'Sync', exact: true }).waitFor({ state: 'detached' });

  // The desk offers one type per declared widget, grouped under the app.
  await page.goto(base + '/');
  await page.locator('.desk-grid').waitFor();
  await add.click();
  await library.waitFor();
  await library.getByRole('heading', { name: 'Widget Fixture', exact: true }).waitFor();
  await library.getByRole('button', { name: /^Sync/ }).click();
  await library.waitFor({ state: 'detached' });
  const widget = page.getByRole('region', { name: 'Sync', exact: true });
  await widget.waitFor();
  // Rendered by the host, from the published summary, always naming the app.
  const text = await widget.innerText();
  assert.match(text, /Widget Fixture/);
  assert.match(text, /73/);
  assert.match(text, /queued since 02:14/);
  await done.click();
  await arrange.waitFor();

  // The rail raises its dot for the app that asked for attention.
  await page.reload();
  await page.locator('.desk-grid').waitFor();
  await page.locator('.rail a[href="/app/widget-fixture"] .rail-dot').waitFor({ timeout: 5000 });

  // Uninstalling takes the summary and the widget with it, rather than leaving
  // a frame that can never render again.
  const removed = await page.evaluate(async () => {
    const session = await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } });
    const { token } = await session.json();
    const response = await fetch('/api/apps/widget-fixture', {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${token}` },
    });
    const all = await (
      await fetch('/api/widgets', { headers: { Authorization: `Bearer ${token}` } })
    ).json();
    return { ok: response.ok, widgets: all.widgets };
  });
  assert.ok(removed.ok);
  assert.deepEqual(removed.widgets, []);
  await page.reload();
  await page.locator('.desk-grid').waitFor();
  assert.ok(!(await labels(page)).includes('Sync'), await labels(page));

  // --- the phone board and Personalise ------------------------------------
  // The phone board is its own seeded board: the time, what needs you, the app
  // grid and Ask.
  for (const size of [
    { width: 320, height: 720, phone: true },
    { width: 390, height: 844, phone: true },
    // 768 is still inside the 860px phone threshold; 900 is the first width
    // that gets the desktop board, which is the point of having two.
    { width: 768, height: 1024, phone: true },
    { width: 900, height: 420, phone: false },
  ]) {
    await page.setViewportSize(size);
    await page.goto(base + '/');
    await page.locator('.desk-grid').waitFor();
    const names = await labels(page);
    assert.deepEqual(
      names,
      size.phone
        ? ['Clock', 'Needs you', 'Your apps', 'Ask']
        : ['Clock', 'Your apps', 'Running now', 'Needs you', 'Ask'],
      `the board at ${size.width}x${size.height}`,
    );
    // Nothing is flagged, so "Needs you" says so rather than listing anything.
    if (size.phone) {
      assert.match(
        await page.getByRole('region', { name: 'Needs you', exact: true }).innerText(),
        /Everything.s running/,
      );
    }
    const overflow = await page.evaluate(() => {
      const content = document.querySelector('.workspace-content');
      return {
        body: document.documentElement.scrollWidth - innerWidth,
        content: content.scrollWidth - content.clientWidth,
      };
    });
    assert.ok(
      overflow.body <= 1 && overflow.content <= 1,
      `phone board at ${size.width}: ${JSON.stringify(overflow)}`,
    );
  }

  // Personalise: a real setting each time, focus returned on Escape.
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.goto(base + '/');
  await page.locator('.desk-grid').waitFor();
  const personalise = {
    click: async () => {
      await deskOptions.click();
      await menuItem('Personalise').click();
    },
  };
  await personalise.click();
  const sheet = page.getByRole('dialog', { name: 'Personalise', exact: true });
  await sheet.waitFor();
  const wallpaperOf = () =>
    page.evaluate(
      () => getComputedStyle(document.querySelector('.shell'), '::before').backgroundImage,
    );
  assert.match(await wallpaperOf(), /wallpapers\/choroni\.jpg/);

  // The painted set previews as real thumbnails rather than empty swatches, so
  // a picture can be chosen by looking at it. Gradients keep their CSS preview.
  const painted = await sheet.evaluate((panel) =>
    [...panel.querySelectorAll('.personalise-wall')]
      .filter((wall) =>
        getComputedStyle(wall.querySelector('.personalise-wall-preview')).backgroundImage.includes(
          '/wallpapers/thumbs/',
        ),
      )
      .map((wall) => wall.dataset.wallpaper),
  );
  assert.equal(
    painted.filter((id) => id !== 'daily').length,
    8,
    `painted wallpapers with a thumbnail: ${painted.join(', ')}`,
  );
  assert.ok(painted.includes('daily'), 'Daily previews the picture it would draw today');

  // Choosing a painted wallpaper changes the picture the shell draws, and the
  // tone hint rides along so the overlay can keep widget text readable.
  await sheet.getByRole('button', { name: /^Páramo/ }).click();
  await page.waitForFunction(() => document.body.dataset.deskWallpaper === 'paramo');
  assert.match(await wallpaperOf(), /wallpapers\/paramo\.jpg/);
  assert.equal(await page.evaluate(() => document.body.dataset.deskTone), 'dark');

  // Daily is a standing choice, not a picture: it stays selected while the id
  // underneath it follows the date.
  await sheet.getByRole('button', { name: /^Daily/ }).click();
  await page.waitForFunction(() => document.body.dataset.deskChoice === 'daily');
  assert.equal(
    await sheet.getByRole('button', { name: /^Daily/ }).getAttribute('aria-pressed'),
    'true',
    'Daily stays the selected choice, not the picture it resolved to',
  );

  // Checking which picture Daily lands on means fixing a date. The frozen clock
  // gets a page of its own, because the rest of this suite needs a moving one.
  // April 8th is the 98th day and the painted set has eight pictures, so the
  // rotation lands on Médanos.
  // The page carries the first one's storage, so it brings the hub session and
  // the welcome flag along rather than meeting a login screen.
  const datedContext = await browser.newContext({
    viewport: { width: 1366, height: 900 },
    storageState: await page.context().storageState(),
  });
  const dated = await datedContext.newPage();
  await dated.addInitScript((stamp) => {
    const fixed = new Date(stamp).getTime();
    const Real = Date;
    globalThis.Date = class extends Real {
      constructor(...args) {
        super(...(args.length ? args : [fixed]));
      }
      static now() {
        return fixed;
      }
    };
  }, '2026-04-08T10:00:00');
  await dated.goto(base + '/');
  await dated.locator('.desk-grid').waitFor();
  // The board draws before the stored preferences arrive, so the flags start at
  // the default and settle a moment later. Waiting for the stored choice is what
  // makes the picture underneath it worth asserting.
  await dated.waitForFunction(() => document.body.dataset.deskChoice === 'daily');
  assert.equal(
    await dated.evaluate(() => document.body.dataset.deskWallpaper),
    'medanos',
    'Daily resolves by the date',
  );
  await datedContext.close();

  await sheet.getByRole('button', { name: /^Night/ }).click();
  await page.waitForFunction(() => document.body.dataset.deskWallpaper === 'night');
  assert.match(await wallpaperOf(), /gradient/);

  // The display toggles are real settings the server keeps, not previews.
  const labelsToggle = sheet.getByLabel('Show app names');
  await labelsToggle.uncheck();
  assert.equal(await labelsToggle.isChecked(), false);
  await page.keyboard.press('Escape');
  await sheet.waitFor({ state: 'detached' });
  await page.reload();
  await page.locator('.desk-grid').waitFor();
  await personalise.click();
  await sheet.waitFor();
  assert.equal(
    await sheet.getByLabel('Show app names').isChecked(),
    false,
    'a display choice survives a reload',
  );
  await sheet.getByLabel('Show app names').check();
  assert.equal(
    await page.evaluate(() => document.body.dataset.deskDim),
    'on',
    'dimming is on until it is turned off',
  );
  await sheet.getByLabel('Dim the wallpaper').uncheck();
  await page.waitForFunction(() => document.body.dataset.deskDim === 'off');
  await sheet.getByLabel('Dim the wallpaper').check();

  // The Ask toggle adds and removes that widget from this board, and it stays.
  // This one edits the board rather than a preference, so it saves to the
  // server before the switch settles; the widget going away is the signal.
  await sheet.getByLabel('Ask on this board').click();
  await page.getByRole('region', { name: 'Ask', exact: true }).waitFor({ state: 'detached' });
  await page.keyboard.press('Escape');
  await sheet.waitFor({ state: 'detached' });
  assert.equal(
    await page.evaluate(() => document.activeElement.getAttribute('aria-label')),
    'Desk options',
    'closing Personalise returns focus to the desk-options control that opened it',
  );
  await page.reload();
  await page.locator('.desk-grid').waitFor();
  assert.ok(!(await labels(page)).includes('Ask'), await labels(page));
  await personalise.click();
  await sheet.waitFor();
  await sheet.getByLabel('Ask on this board').click();
  await page.getByRole('region', { name: 'Ask', exact: true }).waitFor();
  await sheet.getByRole('button', { name: /^Choroní/ }).click();
  await page.keyboard.press('Escape');
  await sheet.waitFor({ state: 'detached' });

  // It fits a phone, where long-pressing bare wallpaper is the way in.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(base + '/');
  await page.locator('.desk-grid').waitFor();
  await page.evaluate(() => {
    const host = document.querySelector('.desk-grid');
    const box = host.getBoundingClientRect();
    host.dispatchEvent(
      new PointerEvent('pointerdown', {
        bubbles: true,
        pointerId: 2,
        pointerType: 'touch',
        clientX: box.left + box.width / 2,
        clientY: box.bottom - 4,
      }),
    );
  });
  // A long press opens the wallpaper menu; Personalise is one of its items.
  await page.getByRole('menu', { name: 'Desk options' }).waitFor({ timeout: 4000 });
  await menuItem('Personalise').click();
  await sheet.waitFor({ timeout: 4000 });
  const sheetBox = await sheet.boundingBox();
  assert.ok(
    sheetBox.x >= -1 && sheetBox.x + sheetBox.width <= 391,
    `Personalise at 390px: ${JSON.stringify(sheetBox)}`,
  );
  await page.keyboard.press('Escape');
  await sheet.waitFor({ state: 'detached' });
  await page.setViewportSize({ width: 1366, height: 900 });

  assert.deepEqual(errors, []);
  console.log(
    'PASS: the desk adds, moves, resizes, undoes, redoes, saves and reloads a widget; the health ' +
      'widget reports the last sweep and runs one on request; keyboard ' +
      'arrangement is announced; Cancel restores; leaving with unsaved changes asks; removing ' +
      'persists; the phone board stays its own; long-press arranges; no overflow at 320/390; ' +
      'an app declares, publishes and renders a widget, raises the rail dot, and loses both on ' +
      'uninstall; the phone board is its own at 320/390/768 and the desktop board at 900; ' +
      'Personalise changes the wallpaper, the labels and the Ask widget, and opens by long-press',
  );
} finally {
  await browser?.close();
  server.kill();
  if (server.exitCode === null) await new Promise((resolve) => server.once('exit', resolve));
}
