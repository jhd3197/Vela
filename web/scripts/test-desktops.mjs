import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

// Desktops against a real engine: creating one, arranging and dressing it
// separately from the first, keeping the choice per device, and keeping
// installed apps out of it. It runs on a disposable data directory, so it never
// touches the user's own desk or installed apps.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const python =
  process.env.VELA_TEST_PYTHON ||
  path.join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const port = 17716;
const server = spawn(python, ['scripts/serve-release-fixtures.py', String(port)], {
  cwd: root,
  windowsHide: true,
  stdio: ['ignore', 'pipe', 'pipe'],
});
const base = `http://127.0.0.1:${port}`;
let output = '',
  browser;
server.stderr.on('data', (data) => {
  output += data;
});
server.on('error', (error) => {
  output += error.message;
});

const labels = (page) =>
  page.evaluate(() =>
    [...document.querySelectorAll('.desk-frame')].map((frame) => frame.getAttribute('aria-label')),
  );

/** Wait until the board on screen is the one expected. Switching desktops
    replaces the board, and the rail's name changes first, so waiting for "a
    frame" would read the previous desktop's. */
const waitForLabels = (page, expected) =>
  page.waitForFunction(
    (want) =>
      JSON.stringify(
        [...document.querySelectorAll('.desk-frame')].map((frame) =>
          frame.getAttribute('aria-label'),
        ),
      ) === want,
    JSON.stringify(expected),
  );

/** The name on the rail's desktop entry. Its tooltip is only visible on hover,
    so this reads the text rather than waiting for it to be shown. */
const desktopName = (page) => page.locator('.rail-desktop-item .rail-tip').innerText();

const waitForDesktop = (page, name) =>
  page.waitForFunction(
    (expected) =>
      document.querySelector('.rail-desktop-item .rail-tip')?.textContent?.trim() === expected,
    name,
  );

/** Open the rail's desktop menu and wait for it. */
async function openMenu(page) {
  await page.locator('.rail-desktop-item').click();
  await page.locator('.desktop-menu').waitFor();
}

/** Create a desktop through the menu, returning once the desk is showing it. */
async function createDesktop(page, name) {
  await openMenu(page);
  await page.getByRole('button', { name: 'New desktop' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.waitFor();
  if (name) await dialog.getByLabel('Name').fill(name);
  await dialog.getByRole('button', { name: 'Create' }).click();
  await dialog.waitFor({ state: 'detached' });
}

/** Choose a desktop by name and wait for its board. */
async function choose(page, name) {
  await openMenu(page);
  await page.locator('.desktop-menu-choice', { hasText: name }).click();
  await page.locator('.desktop-menu').waitFor({ state: 'detached' });
  await waitForDesktop(page, name);
}

try {
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw new Error(output);
    try {
      if ((await fetch(base + '/api/health')).ok) break;
    } catch {
      /* not up yet */
    }
    if (i === 99) throw new Error(`Fixture server did not start: ${output}`);
    await new Promise((resolve) => setTimeout(resolve, 100));
  }

  browser = await chromium.launch({
    headless: true,
    channel: process.env.VELA_BROWSER_CHANNEL || 'chrome',
  });
  const context = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  await context.addInitScript(() => localStorage.setItem('vela.welcome.v1', 'done'));
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));

  await page.goto(base + '/');
  await page.locator('.desk-grid').waitFor();

  // --- the desk you already had -------------------------------------------
  await page.locator('.rail-desktop-item').waitFor();
  const firstBoard = await labels(page);
  assert.ok(firstBoard.includes('Clock'), `the migrated desk is here: ${firstBoard}`);

  // --- a second desktop is a separate workspace ---------------------------
  await createDesktop(page, 'Taxes');
  await waitForDesktop(page, 'Taxes');
  await page.locator('.desk-empty').waitFor();
  assert.deepEqual(
    await labels(page),
    [],
    'a new desktop starts empty rather than copying the first one',
  );

  // Put something on it, and check the first one is untouched.
  await page.getByRole('button', { name: 'Desk options' }).click();
  await page.getByRole('menuitem', { name: 'Add widget' }).click();
  await page.getByRole('dialog').waitFor();
  await page.getByRole('button', { name: /^Clock/ }).click();
  await page.getByRole('dialog').waitFor({ state: 'detached' });
  await page.getByRole('button', { name: 'Done' }).click();
  await page.locator('.desk-frame[aria-label="Clock"]').waitFor();

  await choose(page, 'Desktop 1');
  await waitForLabels(page, firstBoard).catch(async () => {
    assert.deepEqual(
      await labels(page),
      firstBoard,
      'arranging one desktop leaves the other exactly as it was',
    );
  });

  // --- each desktop is dressed its own way --------------------------------
  await choose(page, 'Taxes');
  await page.locator('.desk-frame[aria-label="Clock"]').waitFor();
  await page.getByRole('button', { name: 'Desk options' }).click();
  await page.getByRole('menuitem', { name: 'Personalise' }).click();
  const sheet = page.getByRole('dialog', { name: 'Personalise' });
  await sheet.waitFor();
  assert.match(await sheet.innerText(), /Taxes/, 'the sheet says which desktop it is changing');
  await sheet.getByRole('button', { name: /^Canaima/ }).click();
  await page.waitForFunction(() => document.body.dataset.deskWallpaper === 'canaima');
  await page.keyboard.press('Escape');
  await sheet.waitFor({ state: 'detached' });

  await choose(page, 'Desktop 1');
  await page.waitForFunction(() => document.body.dataset.deskWallpaper === 'choroni');

  // --- it all survives a reload -------------------------------------------
  await page.reload();
  await page.locator('.desk-grid').waitFor();
  await page.waitForFunction(() => document.body.dataset.deskWallpaper === 'choroni');
  await choose(page, 'Taxes');
  await page.locator('.desk-frame[aria-label="Clock"]').waitFor();
  await page.waitForFunction(() => document.body.dataset.deskWallpaper === 'canaima');

  // --- the choice belongs to this browser, not to the server --------------
  const other = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  await other.addInitScript(() => localStorage.setItem('vela.welcome.v1', 'done'));
  const second = await other.newPage();
  await second.goto(base + '/');
  await second.locator('.desk-grid').waitFor();
  await second.locator('.rail-desktop-item').waitFor();
  assert.equal(await desktopName(second), 'Desktop 1', 'another device keeps its own selection');
  assert.equal(await desktopName(page), 'Taxes', 'and choosing there did not move this one');

  // A link straight to one workspace selects it for that browser.
  const taxesId = await second.evaluate(async () => {
    const { token } = await (
      await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } })
    ).json();
    const list = await (
      await fetch('/api/desktops', { headers: { Authorization: `Bearer ${token}` } })
    ).json();
    return list.desktops.find((desktop) => desktop.name === 'Taxes').id;
  });
  await second.goto(`${base}/desktops/${taxesId}`);
  await second.locator('.desk-frame[aria-label="Clock"]').waitFor();
  assert.equal(await desktopName(second), 'Taxes');

  // An id that names nothing gets a way out rather than an empty board.
  await second.goto(`${base}/desktops/${'0'.repeat(32)}`);
  await second.getByText('That desktop is not here').waitFor();
  await second.getByRole('link', { name: 'Go to your desk' }).click();
  await second.locator('.desk-grid').waitFor();
  await other.close();

  // --- the menu works from the keyboard -----------------------------------
  await page.goto(base + '/');
  await page.locator('.desk-grid').waitFor();
  const trigger = page.locator('.rail-desktop-item');
  await trigger.focus();
  await page.keyboard.press('Enter');
  await page.locator('.desktop-menu').waitFor();
  assert.equal(
    await page.evaluate(() => document.activeElement.dataset.selected),
    'true',
    'the keyboard lands on the desktop already selected',
  );
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  await page.locator('.desktop-menu').waitFor({ state: 'detached' });
  assert.equal(
    await page.evaluate(() => document.activeElement.className.includes('rail-desktop-item')),
    true,
    'closing the menu puts focus back on what opened it',
  );

  // Escape closes it without choosing.
  const before = await desktopName(page);
  await openMenu(page);
  await page.keyboard.press('Escape');
  await page.locator('.desktop-menu').waitFor({ state: 'detached' });
  assert.equal(await desktopName(page), before);

  // --- renaming, and losing a race ----------------------------------------
  await openMenu(page);
  await page
    .getByRole('button', { name: /^Rename / })
    .last()
    .click();
  const renameDialog = page.getByRole('dialog');
  await renameDialog.waitFor();
  // Something else renames it first, so this save is working from a revision
  // the server has already moved past.
  await page.evaluate(async () => {
    const { token } = await (
      await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } })
    ).json();
    const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
    const list = await (await fetch('/api/desktops', { headers })).json();
    const target = list.desktops[list.desktops.length - 1];
    await fetch(`/api/desktops/${target.id}`, {
      method: 'PATCH',
      headers,
      body: JSON.stringify({ name: 'Renamed elsewhere', revision: target.revision }),
    });
  });
  await renameDialog.getByLabel('Name').fill('Mine');
  await renameDialog.getByRole('button', { name: 'Rename' }).click();
  await renameDialog.getByRole('alert').waitFor();
  assert.match(
    await renameDialog.getByRole('alert').innerText(),
    /changed somewhere else/i,
    'a rename that lost the race says so instead of overwriting',
  );
  await page.keyboard.press('Escape');
  await renameDialog.waitFor({ state: 'detached' });

  // --- deleting a desktop leaves the apps alone ---------------------------
  const kept = await page.evaluate(async () => {
    const { token } = await (
      await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } })
    ).json();
    const headers = { Authorization: `Bearer ${token}` };
    return (await (await fetch('/api/apps', { headers })).json()).apps.map((app) => app.id);
  });
  await openMenu(page);
  await page
    .getByRole('button', { name: /^Delete / })
    .last()
    .click();
  const confirm = page.getByRole('dialog');
  await confirm.waitFor();
  assert.match(await confirm.innerText(), /apps and everything they have saved stay/i);
  await confirm.getByRole('button', { name: 'Delete desktop' }).click();
  await confirm.waitFor({ state: 'detached' });
  await waitForDesktop(page, 'Desktop 1');
  assert.deepEqual(
    await page.evaluate(async () => {
      const { token } = await (
        await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } })
      ).json();
      const headers = { Authorization: `Bearer ${token}` };
      return (await (await fetch('/api/apps', { headers })).json()).apps.map((app) => app.id);
    }),
    kept,
    'deleting a workspace is not uninstalling anything',
  );

  // The last desktop cannot be deleted, so the control is not offered.
  await openMenu(page);
  assert.equal(await page.locator('.desktop-menu-row').count(), 1);
  assert.equal(await page.locator('.desktop-menu-action:disabled').count(), 1);
  await page.keyboard.press('Escape');

  // --- All apps opens over the desk rather than replacing it --------------
  await page.locator('.rail').getByRole('button', { name: 'All apps' }).click();
  await page.locator('.apps-overlay .launchpad').waitFor();
  assert.equal(
    await page.locator('.desk-grid').count(),
    1,
    'the desk stays mounted underneath the grid',
  );
  assert.equal(
    await page.locator('.shell > .workspace').getAttribute('aria-hidden'),
    'true',
    'the page underneath is scenery while the grid is up',
  );
  await page.locator('.launchpad input[type="search"]').fill('zzzz-no-such-app');
  await page.locator('.launch-empty').getByText('zzzz-no-such-app', { exact: false }).waitFor();
  await page.keyboard.press('Escape');
  await page.locator('.apps-overlay').waitFor({ state: 'detached' });
  assert.equal(await page.locator('.shell > .workspace').getAttribute('aria-hidden'), null);
  await page.locator('.desk-grid').waitFor();

  assert.deepEqual(errors, []);
  console.log(
    'PASS: the migrated desk as Desktop 1, a second workspace with its own board and wallpaper, ' +
      'per-device selection, a direct link and an unknown one, keyboard menu and focus return, ' +
      'a rename that lost its race, deletion leaving apps installed, and All apps over the desk',
  );
  await context.close();
} finally {
  await browser?.close();
  server.kill();
}
