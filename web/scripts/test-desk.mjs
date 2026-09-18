import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

// The desk against a real engine: adding, moving, resizing, undoing, saving,
// and finding the same arrangement after a reload. It runs on a disposable
// data directory, so it never touches the user's own desk or installed apps.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

/** The theme fixtures, read here so the browser can post them as documents. */
const THEME_FIXTURES = [
  'theme-reaches-out.json',
  'theme-escapes.json',
  'theme-strange-font.json',
  'theme-wrong-schema.json',
  'theme-no-slug.json',
  'theme-bundled-slug.json',
];
async function readFixtures() {
  const entries = await Promise.all(
    THEME_FIXTURES.map(async (name) => [
      name,
      JSON.parse(await readFile(path.join(root, 'web/scripts/fixtures', name), 'utf8')),
    ]),
  );
  return Object.fromEntries(entries);
}
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
  // The board belongs to a desktop, so a fresh page asks which desktop before
  // it can ask for the board. Waiting for the widget rather than for the grid
  // is what makes this about what was saved rather than about that order.
  await page.locator('.desk-frame[aria-label="System"]').waitFor();
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
  // The sweep's result is a ring: how many checks passed out of how many ran,
  // with the count in the middle and the verdict under it.
  const ring = health.locator('.vela-ring');
  await ring.waitFor();
  const verdict = await ring.locator('.vela-ring-value').innerText();
  assert.match(verdict, /^\d+\/\d+$/, `the health widget must report the sweep: ${verdict}`);
  const [passing, considered] = verdict.split('/').map(Number);
  assert.ok(considered > 0, 'the sweep considered no checks');
  assert.ok(passing <= considered, `${passing} passing of ${considered} considered`);
  // And it says the same thing to a screen reader as it draws.
  assert.equal(await ring.getAttribute('aria-label'), `${passing} of ${considered} checks passing`);
  await health.locator('.vela-ring-caption').waitFor();
  // Whatever it found, it offers the way to the section that can act on it.
  await health.getByRole('link', { name: /Health|Fix it/ }).waitFor();

  await arrange.click();
  await page.getByRole('button', { name: 'Menu for Health', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Remove', exact: true }).click();
  await done.click();
  await arrange.waitFor();

  // --- every core widget draws on the recipe, on a server with nothing -----
  //
  // This disposable engine has no apps, no automations, no backups and no
  // volumes, which is the state a widget is most likely to be written wrong
  // for: the empty one. A widget that throws here is caught by its boundary
  // and says so, so the check is that none of them does, and that each draws
  // the header the prototype's recipe calls for.
  const EVERY_WIDGET = [
    ['System', 'neutral'],
    ['Volume', 'cyan'],
    ['Flows', 'accent'],
    ['Backups', 'cyan'],
  ];
  for (const [name] of EVERY_WIDGET) {
    await add.click();
    await library.waitFor();
    await library.getByRole('searchbox', { name: 'Find a widget' }).fill(name);
    await library.getByRole('button', { name: new RegExp(`^${name}`) }).click();
    await library.waitFor({ state: 'detached' });
  }
  await done.click();
  await arrange.waitFor();

  for (const [name, tone] of EVERY_WIDGET) {
    const frame = page.getByRole('region', { name: new RegExp(`^${name}`) }).first();
    await frame.waitFor();
    // The header is the widget's own content, tinted by what the widget is
    // about, and present in view mode where the frame draws no chrome at all.
    const head = frame.locator('.vela-card-head').first();
    await head.waitFor();
    assert.equal(
      await head.locator('.vela-card-icon').getAttribute('data-tone'),
      tone,
      `${name} should carry the ${tone} tone`,
    );
    // A Volume widget with no volume chosen still says what it is: its own
    // title is derived from a configuration it does not have yet.
    assert.equal(
      await frame.locator('.vela-card-title').first().innerText(),
      name,
      `${name} should title its own card`,
    );
    assert.equal(
      await frame.getByText('This widget could not be shown.').count(),
      0,
      `${name} threw on a server with nothing to show`,
    );
  }
  // The clock is the one widget with no header: the time is its own heading.
  assert.equal(
    await page
      .getByRole('region', { name: 'Clock', exact: true })
      .locator('.vela-card-head')
      .count(),
    0,
    'the clock should not label itself',
  );

  await arrange.click();
  for (const [name] of EVERY_WIDGET) {
    await page
      .getByRole('button', { name: new RegExp(`^Menu for ${name}`) })
      .first()
      .click();
    await page.getByRole('menuitem', { name: 'Remove', exact: true }).click();
  }
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
    ['sync', 'queued', 'week', 'ledger'],
  );
  assert.deepEqual(
    installed.widgets.map((widget) => widget.layout),
    ['stat', 'list', 'chart', 'keyvalue'],
    'the two new layouts must survive manifest validation',
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

  // --- the two layouts an app can publish a chart and a table with ---------
  await add.click();
  await library.waitFor();
  await library.getByRole('button', { name: /^This week/ }).click();
  await library.waitFor({ state: 'detached' });
  const chart = page.getByRole('region', { name: 'This week', exact: true });
  await chart.waitFor();
  const bars = chart.locator('.vela-bar');
  assert.equal(await bars.count(), 7, 'a chart draws one bar per published point');
  // The last bar carries the strongest step, whatever its value: it is today.
  assert.equal(await bars.last().getAttribute('data-weight'), 'last');
  // The published domain is [0, 12] and the tallest point is 11, so no bar is
  // full height -- a series scaled to itself would have made one.
  const heights = await bars.evaluateAll((nodes) =>
    nodes.map((node) => Number.parseFloat(node.style.height)),
  );
  assert.ok(Math.max(...heights) < 100, `the published domain was ignored: ${heights}`);
  await chart.getByText('syncs per day').waitFor();

  await add.click();
  await library.waitFor();
  await library.getByRole('button', { name: /^Ledger/ }).click();
  await library.waitFor({ state: 'detached' });
  const ledger = page.getByRole('region', { name: 'Ledger', exact: true });
  await ledger.waitFor();
  assert.equal(await ledger.locator('.vela-kv-row').count(), 2);
  await ledger.getByText('Groceries').waitFor();
  await ledger.getByText('$412').waitFor();
  // Both are drawn on the same card as a widget Vela wrote itself.
  for (const region of [chart, ledger]) {
    assert.equal(await region.locator('.vela-card').count(), 1);
    assert.match(await region.innerText(), /Widget Fixture/);
  }
  assert.match(text, /73/);
  assert.match(text, /queued since 02:14/);
  await done.click();
  await arrange.waitFor();

  // The rail raises its dot for the app that asked for attention. A rail app
  // entry is a button that opens a window, not a link to the full-screen page,
  // so it is found by the tip that names it.
  const railDot = () =>
    page
      .locator('.rail .rail-item')
      .filter({ has: page.locator('.rail-tip', { hasText: 'Widget Fixture' }) })
      .locator('.rail-dot');
  await page.reload();
  await page.locator('.desk-grid').waitFor();
  await railDot().waitFor({ timeout: 5000 });

  // --- Needs you: acting on an item, and putting it aside ------------------

  // The flagged app is listed with the actions it was granted and a Later, and
  // Later takes it off the desk and takes the rail's dot with it. The summary
  // itself is untouched, which is why the server still publishes it.
  const needsYou = page.getByRole('region', { name: 'Needs you', exact: true });
  await needsYou.waitFor();

  // Needs you and the rail's Settings dot read the same health sweep, through
  // the one operations list, so they agree with the engine and with each other
  // whichever way the sweep came out on this machine.
  const sweep = await page.evaluate(async () => {
    const session = await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } });
    const { token } = await session.json();
    return (await fetch('/api/doctor', { headers: { Authorization: `Bearer ${token}` } })).json();
  });
  const failing = (sweep.checks || []).filter((check) => check.status === 'fail').length;
  assert.equal(
    await needsYou.locator('.vela-row', { hasText: 'Health check' }).count(),
    failing ? 1 : 0,
    `the sweep reported ${failing} failing check(s); Needs you must say the same`,
  );
  assert.equal(
    await page.locator('.rail-foot .rail-dot').count(),
    failing ? 1 : 0,
    'the rail dot reads the same sweep as the widget',
  );
  const flaggedRow = needsYou.locator('.vela-row', { hasText: 'Widget Fixture' });
  await flaggedRow.waitFor();
  await flaggedRow.getByRole('button', { name: 'Later', exact: true }).click();
  await flaggedRow.waitFor({ state: 'detached' });
  await railDot().waitFor({ state: 'detached', timeout: 5000 });

  const afterLater = await page.evaluate(async () => {
    const session = await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } });
    const { token } = await session.json();
    const all = await (
      await fetch('/api/widgets', { headers: { Authorization: `Bearer ${token}` } })
    ).json();
    const entry = all.widgets.find((w) => w.appId === 'widget-fixture' && w.summary?.attention);
    return { attention: Boolean(entry), snoozed: Boolean(entry?.snoozedUntil) };
  });
  assert.ok(afterLater.attention, 'Later does not clear what the app published');
  assert.ok(afterLater.snoozed, 'Later marks the summary snoozed for the desk');

  // It survives a reload — this is a setting on the server, not a hidden row.
  await page.reload();
  await page.locator('.desk-grid').waitFor();
  await needsYou.waitFor();
  assert.equal(
    await needsYou.locator('.vela-row', { hasText: 'Widget Fixture' }).count(),
    0,
    'a snoozed item stays put aside across a reload',
  );

  // When the snooze runs out it comes back, rather than being dismissed for good.
  await page.evaluate(async () => {
    const session = await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } });
    const { token } = await session.json();
    await fetch('/api/widgets/widget-fixture/sync/snooze', {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${token}` },
    });
  });
  await page.reload();
  await page.locator('.desk-grid').waitFor();
  await needsYou.locator('.vela-row', { hasText: 'Widget Fixture' }).waitFor();
  await railDot().waitFor({ timeout: 5000 });

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

  // --- the status strip ---------------------------------------------------

  // One line along the bottom of the board, drawn from what the server can
  // actually report. It says how this server can be reached, which on a test
  // fixture bound to loopback is "Local", and it never shows a placeholder for
  // something it does not have.
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.goto(base + '/');
  await page.locator('.desk-grid').waitFor();
  const strip = page.getByRole('status', { name: 'This server' });
  await strip.waitFor();
  const stripText = await strip.innerText();
  assert.match(stripText, /Local|LAN only|HTTPS/, `connection mode in: ${stripText}`);
  assert.doesNotMatch(stripText, /undefined|NaN|—/, `the strip invents nothing: ${stripText}`);

  // It is the board's own line, so arranging puts it away rather than leaving
  // it under a widget being dragged.
  await arrange.click();
  await done.waitFor();
  await strip.waitFor({ state: 'detached' });
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await arrange.waitFor();
  await strip.waitFor();

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
  // A panel sliding in over the page lifts off it, at the top of the elevation
  // scale, from the token rather than from a shadow written at the call site.
  const lift = await page
    .locator('.drawer')
    .first()
    .evaluate((drawer) => ({
      drawer: getComputedStyle(drawer).boxShadow,
      token: getComputedStyle(document.documentElement).getPropertyValue('--shadow-lg').trim(),
    }));
  assert.ok(lift.drawer && lift.drawer !== 'none', 'the drawer draws no elevation');
  assert.ok(lift.token, '--shadow-lg resolved to nothing');
  const wallpaperOf = () =>
    page.evaluate(
      () => getComputedStyle(document.querySelector('.shell'), '::before').backgroundImage,
    );
  assert.match(await wallpaperOf(), /wallpapers\/choroni\.jpg/);

  // --- Theme: a real repaint, held across a reload, with no flash ----------
  //
  // The Theme row sits beside Wallpaper because they are the same kind of
  // choice. What it changes is different: a wallpaper is a picture behind the
  // board, a theme is what every surface in the dashboard is made of.
  const ground = () =>
    page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--bg').trim());
  const stockGround = await ground();

  const swatches = sheet.locator('.personalise-theme');
  await swatches.first().waitFor();
  assert.ok((await swatches.count()) >= 6, 'the bundled set is six or more themes');
  // Every strip is drawn from the theme's own tokens, so a theme shows what it
  // will do rather than a picture of what it once did.
  const strips = await sheet.evaluate((panel) =>
    [...panel.querySelectorAll('.personalise-theme')].map((theme) => ({
      slug: theme.dataset.themeSlug,
      colours: [...theme.querySelectorAll('.personalise-theme-swatch')].map(
        (swatch) => getComputedStyle(swatch).backgroundColor,
      ),
    })),
  );
  assert.ok(
    strips.every((theme) => theme.colours.length >= 4),
    JSON.stringify(strips),
  );
  assert.equal(
    new Set(strips.map((theme) => theme.colours.join())).size,
    strips.length,
    'two themes drew the same strip',
  );

  await sheet.locator('.personalise-theme[data-theme-slug="contraste"]').click();
  await page.waitForFunction(
    (was) => getComputedStyle(document.documentElement).getPropertyValue('--bg').trim() !== was,
    stockGround,
  );
  const pickedGround = await ground();
  assert.notEqual(pickedGround, stockGround);

  // It is a setting, not a page state.
  await page.waitForFunction(async () => {
    const response = await fetch('/api/settings');
    return (await response.json()).theme_id === 'contraste';
  });

  // Reload and the theme is on the page before React has put anything in it:
  // the cache paints it, so there is no flash of the stock colours.
  await page.goto(base + '/');
  const beforeMount = await page.evaluate(() => ({
    ground: getComputedStyle(document.documentElement).getPropertyValue('--bg').trim(),
    mounted: document.getElementById('root')?.childElementCount ?? 0,
  }));
  assert.equal(
    beforeMount.ground,
    pickedGround,
    'the chosen theme must paint before the dashboard mounts',
  );
  await page.locator('.desk-grid').waitFor();
  assert.equal(await ground(), pickedGround, 'the theme survived the reload');

  // An app window asking for a dark title bar under a light base gets *this*
  // theme's dark, not the stock one's -- which is what the scoped half of the
  // applier is for.
  //
  // Both bases are checked, and against the theme rather than against "not
  // empty". An element re-asserting a base matches `[data-theme='…']` in the
  // generated stylesheet directly, and a value declared on an element beats one
  // inherited from the root -- so without the applied theme scoped under *both*
  // selectors, such an element silently falls back to the stock look while
  // everything around it wears the chosen one. A weaker assertion here missed
  // exactly that.
  const scoped = await page.evaluate(async () => {
    const session = await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } });
    const { token } = await session.json();
    const response = await fetch('/api/themes/contraste', {
      headers: { Authorization: `Bearer ${token}` },
    });
    const theme = await response.json();
    const measured = {};
    for (const base of theme.bases) {
      const probe = document.createElement('div');
      probe.dataset.theme = base;
      document.body.append(probe);
      measured[base] = getComputedStyle(probe).getPropertyValue('--bg').trim();
      probe.remove();
    }
    return {
      measured,
      wanted: Object.fromEntries(theme.bases.map((base) => [base, theme.tokens[base]['--bg']])),
    };
  });
  assert.deepEqual(
    scoped.measured,
    scoped.wanted,
    'an element re-asserting a base must get the applied theme, not the stock one',
  );

  // Back to stock: the inline tokens go, so the generated stylesheet shows
  // through rather than a copy of itself being written back over it.
  await personalise.click();
  await sheet.waitFor();
  await sheet.locator('.personalise-theme[data-theme-slug="vela"]').click();
  await page.waitForFunction(
    (was) => getComputedStyle(document.documentElement).getPropertyValue('--bg').trim() !== was,
    pickedGround,
  );
  assert.equal(await ground(), stockGround, 'the stock look is the stylesheet, not a copy of it');
  assert.equal(
    await page.evaluate(() => document.documentElement.style.getPropertyValue('--bg')),
    '',
    'selecting the stock theme must leave no inline token behind',
  );

  // --- import, export and remove -------------------------------------------
  //
  // The file picker is driven directly rather than through a real file dialog:
  // what is being checked is the review sheet, the refusals and the fallback,
  // not the operating system's ability to open a window.
  const fixture = (name) => path.join(root, 'web/scripts/fixtures', name);

  await sheet
    .locator('input[aria-label="Choose a theme file"]')
    .setInputFiles(fixture('theme-valid.json'));
  const review = sheet.getByRole('group', { name: 'Review this theme' });
  await review.waitFor();
  // Nothing is applied until it is confirmed: the review names the theme, who
  // made it and how much of the dashboard it would touch.
  await review.getByText('Prestado').waitFor();
  await review.getByText(/by A friend/).waitFor();
  assert.equal(await ground(), stockGround, 'a theme under review must not be applied yet');

  await review.getByRole('button', { name: 'Add this theme', exact: true }).click();
  await page.waitForFunction(
    (was) => getComputedStyle(document.documentElement).getPropertyValue('--bg').trim() !== was,
    stockGround,
  );
  const importedGround = await ground();
  const imported = sheet.locator('.personalise-theme[data-theme-slug="prestado"]');
  await imported.waitFor();
  await imported.getByText('Imported').waitFor();

  // Export gives back exactly what was stored. That it arrives as a download is
  // asserted server-side in tests/test_themes.py: the dashboard's own service
  // worker does not pass `Content-Disposition` through to `fetch`, and what
  // matters here is that the bytes coming back are the theme that went in.
  const exported = await page.evaluate(async () => {
    const session = await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } });
    const { token } = await session.json();
    const response = await fetch('/api/themes/prestado/export', {
      headers: { Authorization: `Bearer ${token}` },
    });
    return { status: response.status, body: await response.json() };
  });
  assert.equal(exported.status, 200, JSON.stringify(exported));
  assert.equal(exported.body.slug, 'prestado');
  assert.equal(exported.body.author, 'A friend');
  assert.deepEqual(exported.body.bases, ['light', 'dark']);

  // A value Vela will not take is dropped and named; the rest of the theme is
  // still imported, because one bad key is not a reason to refuse somebody's
  // work. A theme that breaks a structural rule is refused outright.
  const outcomes = await page.evaluate(
    async (names) => {
      const session = await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } });
      const { token } = await session.json();
      const results = {};
      for (const [name, document] of Object.entries(names)) {
        const response = await fetch('/api/themes/import', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
          body: JSON.stringify(document),
        });
        results[name] = { status: response.status, body: await response.json() };
      }
      return results;
    },
    await readFixtures(),
  );

  assert.equal(outcomes['theme-reaches-out.json'].status, 200);
  assert.deepEqual(outcomes['theme-reaches-out.json'].body.dropped, { light: ['--bg'] });
  assert.deepEqual(outcomes['theme-escapes.json'].body.dropped, { light: ['--bg-card'] });
  assert.deepEqual(outcomes['theme-strange-font.json'].body.dropped, { light: ['--font'] });
  for (const [name, expected] of [
    ['theme-wrong-schema.json', 'schema_version must be 1'],
    ['theme-no-slug.json', 'slug must match'],
    ['theme-bundled-slug.json', 'Vela ships'],
  ]) {
    assert.equal(outcomes[name].status === 200, false, `${name} was accepted`);
    assert.match(outcomes[name].body.detail, new RegExp(expected), name);
  }

  // Removing the theme in use falls back to the stock look without a reload.
  await page.reload();
  await page.locator('.desk-grid').waitFor();
  await personalise.click();
  await sheet.waitFor();
  assert.equal(await ground(), importedGround, 'the imported theme survived the reload');
  await sheet.getByRole('button', { name: 'Remove', exact: true }).click();
  await page.waitForFunction(
    (want) => getComputedStyle(document.documentElement).getPropertyValue('--bg').trim() === want,
    stockGround,
  );
  await sheet
    .locator('.personalise-theme[data-theme-slug="prestado"]')
    .waitFor({ state: 'detached' });

  // --- the Theme row on a phone, and at 200 % zoom -------------------------
  //
  // A swatch strip is the widest thing in the sheet, so it is the first thing
  // that would push the panel sideways. The sheet is already open, so this
  // narrows the window around it rather than reopening it by the phone's own
  // long-press path, which the phone section below already covers. The sheet is
  // still open from the remove above.
  for (const [label, size] of [
    ['a phone', { width: 390, height: 844 }],
    ['a phone at 200%', { width: 195, height: 422 }],
  ]) {
    await page.setViewportSize(size);
    const strip = sheet.locator('.personalise-theme').first();
    await strip.waitFor();
    const overflow = await sheet.evaluate((panel) => ({
      panel: panel.scrollWidth - panel.clientWidth,
      page: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    }));
    assert.equal(overflow.panel, 0, `the theme row overflows the sheet on ${label}`);
    assert.equal(overflow.page, 0, `the theme row overflows the page on ${label}`);
    // And it is reachable: the sheet scrolls to it rather than hiding it.
    await strip.scrollIntoViewIfNeeded();
    assert.ok(await strip.isVisible(), `the theme row is not reachable on ${label}`);
  }
  // Back to the width the rest of this block works at, with the sheet still
  // open: the wallpaper checks below carry on in it.
  await page.setViewportSize({ width: 1366, height: 900 });

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
  // The widget leaves the board as soon as the switch moves, but the point of
  // this check is that the choice survives a reload — so wait for the server to
  // have it rather than racing the save.
  await page.waitForFunction(async () => {
    const session = await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } });
    const { token } = await session.json();
    const desk = await (
      await fetch('/api/desk', { headers: { Authorization: `Bearer ${token}` } })
    ).json();
    return !desk.boards.desktop.widgets.some((widget) => widget.type === 'ask');
  });
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
      'Personalise changes the wallpaper, the labels and the Ask widget, and opens by long-press; a theme repaints the dashboard, is saved, survives a reload without a flash, and leaves no inline token behind when the stock look is chosen; a theme file is reviewed before it is applied, exports as what was stored, drops what Vela will not take and names it, and falls back to stock when removed',
  );
} finally {
  await browser?.close();
  server.kill();
  if (server.exitCode === null) await new Promise((resolve) => server.once('exit', resolve));
}
