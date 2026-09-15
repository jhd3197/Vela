/**
 * Browser acceptance for automations: the real editor against a real server.
 *
 * Runs against a disposable engine with the pinned Notes fixture installed. It
 * builds an automation the way a person would, runs it, reviews and removes the
 * permission it needs, and checks the page fits from a phone to a large screen
 * in both themes.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const python =
  process.env.VELA_TEST_PYTHON ||
  path.join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const server = spawn(python, ['scripts/serve-automation-fixtures.py'], {
  cwd: root,
  windowsHide: true,
  stdio: ['ignore', 'pipe', 'pipe'],
});
const base = 'http://127.0.0.1:17716';
let output = '';
let browser;
server.stderr.on('data', (data) => {
  output += data;
});
server.on('error', (error) => {
  output += error.message;
});

async function ready() {
  for (let attempt = 0; attempt < 150; attempt += 1) {
    if (server.exitCode !== null) throw new Error(output);
    try {
      if ((await fetch(`${base}/api/health`)).ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Fixture server did not start: ${output}`);
}

try {
  await ready();
  const runtime = await (
    await fetch(`${base}/api/automations/status`, {
      headers: {
        'X-Vela-Bootstrap': '1',
        Authorization: `Bearer ${
          (
            await (
              await fetch(`${base}/api/session`, { headers: { 'X-Vela-Bootstrap': '1' } })
            ).json()
          ).token
        }`,
      },
    }).catch(() => ({ json: async () => ({ available: false }) }))
  ).json();

  browser = await chromium.launch({
    headless: true,
    channel: process.env.VELA_BROWSER_CHANNEL || 'chrome',
  });
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  await page.addInitScript(() => localStorage.setItem('vela.welcome.v1', 'done'));
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const shots = path.join(root, 'docs/screenshots/automations');
  await fs.mkdir(shots, { recursive: true });

  /* ---------------------------------------------------- empty state ------ */

  await page.goto(`${base}/automations`);
  await page.getByRole('heading', { name: 'No automations yet' }).waitFor();
  // Nothing invented: an empty server reports zero, not a sample.
  await page.getByText('Nothing has run yet.').waitFor();
  assert.equal(await page.getByText('Preview').count(), 0, 'the preview label must be gone');
  await page.screenshot({ path: path.join(shots, 'empty.png'), fullPage: true });

  /* ---------------------------------------------------- build one -------- */

  await page.getByRole('button', { name: 'New automation' }).first().click();
  const createDialog = page.getByRole('dialog');
  await createDialog.getByLabel('Name').fill('Save a note');
  await createDialog.getByRole('button', { name: 'Create', exact: true }).click();
  await page.waitForURL(/\/automations\/[0-9a-f-]+$/);
  await page.getByText('Run manually').waitFor();

  // Running is unavailable until the automation can actually run.
  await page.getByText('Add a trigger and at least one step').waitFor();
  assert.equal(await page.getByRole('button', { name: 'Run', exact: true }).isDisabled(), true);

  // The picker offers only vetted steps, and no dead ends.
  await page.locator('.tr-plus-btn, [class*="tr-plus"]').first().click();
  const picker = page.locator('.tr-picker');
  await picker.waitFor();
  for (const missing of ['JS Transform', 'HTTP Request', 'AI Prompt', 'Switch']) {
    assert.equal(
      await picker.getByText(missing, { exact: false }).count(),
      0,
      `${missing} must not be offered`,
    );
  }
  // Vela runs no Model Context Protocol steps, so the editor's import tile is
  // not reachable. The server refuses those node types either way.
  assert.equal(
    await picker.locator('.tr-picker__feat--add').isVisible(),
    false,
    'the MCP import tile must not be usable',
  );
  await picker.getByText('Notes', { exact: true }).first().click();
  await picker.getByText('Create note', { exact: true }).click();
  await page.getByText('Notes: Create note').first().waitFor();

  /* ---------------------------------------------------- permission ------- */

  const permission = page.locator('.automation-grants');
  await permission.waitFor();
  await permission.getByText('Notes · Create note').waitFor();
  await permission.getByText(/Adds a new record to Notes/).waitFor();
  await page.screenshot({ path: path.join(shots, 'permission.png'), fullPage: true });

  // Turning it on is refused until the request is allowed.
  await page.getByRole('button', { name: 'Turn on' }).click();
  await page
    .getByText(/Allow this automation to use Notes|still needs a value/)
    .first()
    .waitFor();

  // The inspector generated one field per input the action's schema declares.
  const inspector = page.locator('.tr-rail');
  await page.getByText('Notes: Create note').first().click();
  const field = (label) =>
    inspector.locator('.tr-field').filter({ has: page.locator('label', { hasText: label }) });
  await field('Title').waitFor();
  for (const [label, value] of [
    ['Body', 'Pasta and salad'],
    ['Title', 'Dinner plan'],
  ]) {
    const editor = field(label).locator('.tr-rich__editor');
    await editor.click();
    await editor.pressSequentially(value, { delay: 10 });
    await editor.blur();
  }
  await page.getByText('Saved', { exact: true }).waitFor({ timeout: 15000 });
  // Filling both inputs clears the "before this can run" list.
  await page.locator('.automation-problems').waitFor({ state: 'detached', timeout: 15000 });

  await permission.getByRole('button', { name: 'Allow' }).click();
  await permission.getByRole('button', { name: /Stop allowing/ }).waitFor();

  /* ---------------------------------------------------- run it ----------- */

  if (runtime.available) {
    await page.getByRole('button', { name: 'Run', exact: true }).click();
    await page.getByText('Finished', { exact: true }).first().waitFor({ timeout: 60000 });
    await page
      .getByText(/Notes: Create note.*finished/)
      .first()
      .waitFor();
    await page.screenshot({ path: path.join(shots, 'run.png'), fullPage: true });

    const stored = await page.evaluate(async () => {
      const token = (
        await (await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } })).json()
      ).token;
      const hub = { Authorization: `Bearer ${token}` };
      const session = await (
        await fetch('/api/apps/notes/session', { method: 'POST', headers: hub })
      ).json();
      return (
        await (
          await fetch('/api/app/storage', { headers: { Authorization: `Bearer ${session.token}` } })
        ).json()
      ).value;
    });
    assert.equal(stored.notes.length, 1, 'the automation must create exactly one note');
    assert.equal(stored.notes[0].title, 'Dinner plan');
  } else {
    console.log(`       skipped execution: ${runtime.detail}`);
  }

  /* ---------------------------------------------------- revoke ----------- */

  await page.getByRole('button', { name: /Stop allowing/ }).click();
  await permission.getByRole('button', { name: 'Allow' }).waitFor();
  await page.goto(`${base}/automations`);
  await page.getByRole('link', { name: 'Save a note' }).first().waitFor();

  /* ---------------------------------------------------- layouts ---------- */

  for (const theme of ['light', 'dark']) {
    await page.evaluate(
      (value) => document.documentElement.setAttribute('data-theme', value),
      theme,
    );
    for (const size of [
      { width: 320, height: 720 },
      { width: 390, height: 844 },
      { width: 768, height: 1024 },
      { width: 1024, height: 768 },
      { width: 1920, height: 1080 },
    ]) {
      await page.setViewportSize(size);
      for (const route of [
        '/automations',
        page.url().includes('/automations/') ? page.url() : null,
      ]) {
        if (!route) continue;
        await page.goto(route.startsWith('http') ? route : base + route);
        await page.locator('.rail').waitFor();
        const box = await page.evaluate(() => {
          const content = document.querySelector('.workspace-content');
          return {
            body: document.documentElement.scrollWidth - innerWidth,
            content: content.scrollWidth - content.clientWidth,
          };
        });
        const where = `${route} ${theme} ${size.width}x${size.height}`;
        assert.ok(box.body <= 1, `${where} body overflow: ${JSON.stringify(box)}`);
        assert.ok(box.content <= 1, `${where} content overflow: ${JSON.stringify(box)}`);
      }
    }
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${base}/automations`);
  await page.getByRole('link', { name: 'Save a note' }).first().waitFor();
  await page.screenshot({ path: path.join(shots, 'phone.png'), fullPage: true });
  // The editor itself has to stay usable on a phone: canvas above, everything
  // else stacked below it.
  await page.getByRole('link', { name: 'Save a note' }).first().click();
  await page.locator('.automation-canvas').waitFor();
  await page.getByText('Notes: Create note').first().waitFor();
  await page.screenshot({ path: path.join(shots, 'phone-editor.png'), fullPage: true });

  assert.deepEqual(errors, []);
  console.log(
    'PASS: empty state, building an automation in the real editor, the vetted step picker, ' +
      'permission review, allow and remove' +
      (runtime.available ? ', a run that creates exactly one note' : ' (execution skipped)') +
      ', and 320/390/768/1024/1920 layouts in both themes',
  );
} finally {
  await browser?.close();
  server.kill();
}
