import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

// The Files app against a real engine on a disposable data directory. It never
// touches the user's own files: the only share is the Downloads folder the
// engine creates inside that temporary directory.
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

try {
  for (let i = 0; i < 100; i += 1) {
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
  const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
  await page.addInitScript(() => {
    try {
      localStorage.setItem('vela.welcome.v1', 'done');
    } catch {
      // A sandboxed app frame has no same-origin storage, and needs none.
    }
  });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));

  const rows = () => page.locator('.files-row .files-name');
  const named = (name) => page.locator('.files-row', { hasText: name }).first();

  // --- the default share --------------------------------------------------

  await page.goto(base + '/files');
  // A fresh Vela has one share: the Downloads folder it makes for itself. The
  // list is drawn before the shares arrive, so wait for the share rather than
  // for the container around it.
  await page.locator('.files-share').first().waitFor();
  assert.deepEqual(await page.locator('.files-share').allInnerTexts(), ['Downloads']);
  await page.getByRole('status').filter({ hasText: 'Nothing here yet' }).waitFor();

  // --- making and renaming a folder ---------------------------------------

  await page.getByRole('button', { name: 'New folder', exact: true }).click();
  await page.getByLabel('Name', { exact: true }).fill('Trips');
  await page.getByRole('button', { name: 'Create', exact: true }).click();
  await named('Trips').waitFor();
  assert.deepEqual(await rows().allInnerTexts(), ['Trips']);

  await named('Trips').click();
  await page.getByRole('button', { name: 'Rename', exact: true }).click();
  await page.getByLabel('Name', { exact: true }).fill('Travel');
  await page.getByRole('button', { name: 'Rename', exact: true }).last().click();
  await named('Travel').waitFor();

  // --- uploading, and walking into a folder --------------------------------

  await page.locator('input[type=file]').setInputFiles({
    name: 'note.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('hello from a test'),
  });
  await named('note.txt').waitFor();

  // A folder opens on a double-click and the breadcrumb follows it back.
  await named('Travel').dblclick();
  await page.getByRole('status').filter({ hasText: 'Nothing here yet' }).waitFor();
  const crumbs = page.locator('.files-crumbs button');
  assert.deepEqual(await crumbs.allInnerTexts(), ['Downloads', 'Travel']);
  await crumbs.first().click();
  await named('note.txt').waitFor();

  // --- previewing a text file ---------------------------------------------

  // Text is shown as text rather than rendered, so a shared HTML file is read
  // and never run.
  await named('note.txt').dblclick();
  const preview = page.getByRole('dialog', { name: /Preview of note\.txt/ });
  await preview.waitFor();
  // The bytes are fetched with the session before they can be shown, so the
  // text arrives a moment after the dialog does.
  await preview.locator('.files-preview-text').filter({ hasText: 'hello from a test' }).waitFor();
  assert.match(await preview.locator('.files-preview-text').innerText(), /hello from a test/);
  await preview.getByRole('button', { name: 'Close', exact: true }).click();
  await preview.waitFor({ state: 'detached' });

  // --- deleting goes to the trash -----------------------------------------

  await named('note.txt').click();
  await page.getByRole('button', { name: 'Delete', exact: true }).click();
  await named('note.txt').waitFor({ state: 'detached' });
  const trash = await page.evaluate(async () => {
    const session = await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } });
    const { token } = await session.json();
    const response = await fetch('/api/files-trash', {
      headers: { Authorization: `Bearer ${token}` },
    });
    return response.json();
  });
  assert.equal(trash.entries.length, 1, JSON.stringify(trash));
  assert.match(trash.entries[0].name, /note\.txt$/);
  assert.equal(trash.days, 30);

  // --- the boundary, over the real API ------------------------------------

  // The one rule the whole feature rests on, asked for directly rather than
  // through the page: nothing outside a share is served.
  const escapes = await page.evaluate(async () => {
    const session = await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } });
    const { token } = await session.json();
    const headers = { Authorization: `Bearer ${token}` };
    const attempts = [
      '../../secret.txt',
      '..',
      '/etc/passwd',
      'C:/Windows/win.ini',
      '%2e%2e/%2e%2e/secret.txt',
    ];
    const seen = [];
    for (const attempt of attempts) {
      const response = await fetch(`/api/files/downloads?path=${encodeURIComponent(attempt)}`, {
        headers,
      });
      seen.push({ attempt, status: response.status, body: (await response.text()).slice(0, 80) });
    }
    const unknownShare = await fetch('/api/files/nope', { headers });
    seen.push({ attempt: 'unknown share', status: unknownShare.status, body: '' });
    return seen;
  });
  for (const { attempt, status, body } of escapes) {
    assert.ok(status === 403 || status === 404, `${attempt} should be refused, got ${status}`);
    // A refusal must not leak the real path it refused, either.
    assert.doesNotMatch(body, /Temp|Users|Windows/, `${attempt} named a real path: ${body}`);
  }

  // --- a phone keeps the same page usable ---------------------------------

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(base + '/files');
  await page.locator('.files-share').first().waitFor();
  await named('Travel').waitFor();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  assert.ok(overflow <= 1, `horizontal overflow at 390px: ${overflow}`);

  assert.deepEqual(errors, [], `page errors: ${errors.join(' | ')}`);
  console.log(
    'PASS: the default share, making and renaming a folder, uploading, walking into a folder ' +
      'and back, previewing text as text, deleting to the trash, the share boundary refusing ' +
      'traversal and an unknown share, and a phone layout without overflow',
  );
} finally {
  await browser?.close();
  server.kill();
}
