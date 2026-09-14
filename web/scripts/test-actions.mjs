import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const { chromium } = createRequire(import.meta.url)('playwright');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const python =
  process.env.VELA_TEST_PYTHON ||
  path.join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const server = spawn(python, ['scripts/serve-action-fixtures.py'], {
  cwd: root,
  windowsHide: true,
  stdio: ['ignore', 'pipe', 'pipe'],
});
let output = '',
  browser;
server.stderr.on('data', (data) => (output += data));
const base = 'http://127.0.0.1:17716';
try {
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw Error(output);
    try {
      if ((await fetch(base + '/api/health')).ok) break;
    } catch {}
    if (i === 99) throw Error(output);
    await new Promise((r) => setTimeout(r, 100));
  }
  browser = await chromium.launch({
    headless: true,
    channel: process.env.VELA_BROWSER_CHANNEL || 'chrome',
  });
  const meals = await browser.newPage({ viewport: { width: 1366, height: 900 } }),
    notes = await browser.newPage({
      viewport: { width: 390, height: 844 },
      isMobile: true,
      hasTouch: true,
    });
  const errors = [];
  for (const page of [meals, notes]) page.on('pageerror', (error) => errors.push(error.message));
  await meals.goto(base + '/app/meals');
  await notes.goto(base + '/app/notes');
  const mf = meals.frameLocator('iframe'),
    nf = notes.frameLocator('iframe');
  await mf.locator('#dataStatus').filter({ hasText: 'Saved to your engine' }).waitFor();
  await mf.locator('#sendWeek').click();
  await mf.locator('#actionStatus').filter({ hasText: 'Allow this action' }).waitFor();
  await meals.reload();
  await mf.locator('#retrySend').waitFor();
  await meals.getByText('App actions & activity', { exact: true }).click();
  await meals.getByRole('button', { name: 'Allow action', exact: true }).click();
  await meals.getByRole('button', { name: 'Revoke action', exact: true }).waitFor();
  await meals.getByText('App actions & activity', { exact: true }).click();
  await mf.locator('#retrySend').click();
  await mf.locator('#actionStatus').filter({ hasText: 'Created in Notes' }).waitFor();
  await nf.locator('#titleInput').waitFor();
  for (let i = 0; i < 50 && (await nf.locator('#titleInput').inputValue()) !== 'My meal plan'; i++)
    await new Promise((r) => setTimeout(r, 100));
  assert.equal(await nf.locator('#titleInput').inputValue(), 'My meal plan');
  assert.match(await nf.locator('#bodyInput').inputValue(), /Mon: Berry Overnight Oats/);
  assert.equal(await nf.locator('.note-item').count(), 1);
  await mf.locator('.recipe-card').first().click();
  await mf.locator('#sendRecipe').click();
  await mf.locator('#actionStatus').filter({ hasText: 'Created in Notes' }).waitFor();
  await nf.locator('.note-item').nth(1).waitFor({ state: 'attached' });
  // Hold one Notes save so a cross-app action wins the newer revision.
  let releaseWrite, startedWrite;
  const started = new Promise((resolve) => (startedWrite = resolve)),
    gate = new Promise((resolve) => (releaseWrite = resolve));
  await notes.route('**/api/app/storage', async (route) => {
    if (route.request().method() === 'PUT') {
      startedWrite();
      await gate;
    }
    await route.continue();
  });
  await nf.locator('#bodyInput').fill('Unsaved notes draft retained during action');
  await started;
  const actionDone = meals.waitForResponse(
    (response) =>
      response.url().endsWith('/api/app/actions/invoke') && response.request().method() === 'POST',
  );
  await mf.locator('#sendWeek').click();
  assert.equal((await actionDone).status(), 200);
  releaseWrite();
  await nf.locator('#recovery').waitFor();
  assert.match(await nf.locator('#draftData').inputValue(), /Unsaved notes draft retained/);
  await notes.unroute('**/api/app/storage');
  await nf.locator('#reloadData').click();
  await nf.locator('#recovery').waitFor({ state: 'hidden' });
  assert.equal(await nf.locator('.note-item').count(), 3);
  const shots = path.join(root, 'docs/screenshots/increment-5');
  await fs.mkdir(shots, { recursive: true });
  await meals.getByText('App actions & activity', { exact: true }).click();
  await meals
    .getByText(/meals → notes · create-note · succeeded/)
    .first()
    .waitFor();
  await meals.screenshot({ path: path.join(shots, 'meals-action-desktop.png') });
  await notes.screenshot({ path: path.join(shots, 'notes-mobile.png') });
  await meals.getByRole('button', { name: 'Revoke action', exact: true }).click();
  await mf.locator('#sendWeek').click();
  await mf.locator('#actionStatus').filter({ hasText: 'Allow this action' }).waitFor();
  assert.equal(await nf.locator('.note-item').count(), 3);
  assert.deepEqual(errors, []);
  console.log(
    'PASS: explicit grant, durable send retry after reload, recipe/week to Notes, two clients, stale draft recovery, activity, revocation',
  );
} finally {
  await browser?.close();
  server.kill();
  await new Promise((resolve) =>
    server.exitCode !== null ? resolve() : server.once('exit', resolve),
  );
}
