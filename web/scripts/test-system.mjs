import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

// System against disposable log files in an isolated Vite fixture. It never
// contacts a Vela server and never reads a log off this computer. Screenshots
// go to docs/screenshots/system/.
const web = fileURLToPath(new URL('..', import.meta.url));
const shots = fileURLToPath(new URL('../../docs/screenshots/system', import.meta.url));
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
  const base = `http://127.0.0.1:${port}/scripts/fixtures/system.html`;
  browser = await chromium.launch({
    headless: true,
    channel: process.env.VELA_BROWSER_CHANNEL || 'chrome',
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.addInitScript(() => {
    try {
      localStorage.setItem('vela.welcome.v1', 'done');
    } catch {
      // Blocked site data: the fixture still renders.
    }
  });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await fs.mkdir(shots, { recursive: true });
  const shot = async (name) => page.screenshot({ path: path.join(shots, `${name}.png`) });

  // 1. Developer tools off: System explains itself and offers the switch. It
  //    must not show a log before anyone has turned the preference on.
  await page.goto(base);
  await page.getByRole('heading', { name: 'Developer tools are off' }).waitFor();
  assert.equal(await page.locator('.logs-layout').count(), 0);
  assert.equal(await page.locator('.seg-opt').count(), 0);
  await shot('system-locked');

  await page.getByRole('button', { name: 'Enable developer tools' }).click();

  // 2. Tabs. Overview is the default and keeps the engine card it always had.
  await page.locator('.seg-opt', { hasText: 'Overview' }).waitFor();
  assert.deepEqual(await page.locator('.seg-opt').allInnerTexts(), ['Overview', 'Logs', 'Errors']);
  await page.locator('.engine-card').waitFor();
  assert.ok((await page.locator('.fact-grid .fact').count()) >= 5);
  await shot('system-overview');

  // 3. The Logs tab lists the fixture files, grouped by what wrote them, with
  //    the rotated copy folded under the log it came from.
  await page.locator('.seg-opt', { hasText: 'Logs' }).click();
  await page.locator('.logs-layout').waitFor();
  assert.deepEqual(await page.locator('.logs-group-title').allInnerTexts(), [
    'SERVER',
    'ACTIVITY',
    'APPS',
    'ELSEWHERE',
  ]);
  assert.deepEqual(await page.locator('.logs-file-name').allInnerTexts(), [
    'server.log',
    'server.log.1',
    'audit.log',
    'notes.log',
    'Automation runs',
  ]);
  assert.equal(await page.locator('.logs-file-rotated').count(), 1);

  // The tab and the open log ride in the query, so a reload lands here again.
  assert.match(await page.evaluate(() => window.__location), /tab=logs/);

  // 4. The newest lines are shown, and a warning and an error are marked.
  await page.locator('.logs-line').first().waitFor();
  const status = await page.locator('.logs-status').innerText();
  assert.match(status, /242 lines, showing the last 200/);
  assert.equal(await page.locator('.logs-line-error').count(), 1);
  assert.equal(await page.locator('.logs-line-warn').count(), 1);
  await shot('system-logs');

  // 5. Search narrows to matching lines and marks the match inside them.
  await page.locator('.logs-search input').fill('nearly full');
  await page.waitForFunction(() => document.querySelectorAll('.logs-line').length === 1);
  assert.match(await page.locator('.logs-status').innerText(), /1 matching line/);
  assert.equal(await page.locator('.logs-line-text mark').count(), 1);
  await shot('system-logs-search');

  // A search with no hits says so rather than showing an empty box.
  await page.locator('.logs-search input').fill('nothing matches this');
  await page.getByText('Nothing in this log matches that.').waitFor();

  await page.locator('.logs-search input').fill('');
  await page.waitForFunction(() => document.querySelectorAll('.logs-line').length > 1);

  // 6. `/` focuses the search box from anywhere on the page.
  await page.locator('.logs-content').click();
  await page.keyboard.press('/');
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('type')), 'search');
  await page.keyboard.press('Escape');

  // 7. How many lines to show is the reader's choice.
  await page.locator('.logs-lines-choice select').selectOption('50');
  await page.waitForFunction(() => document.querySelectorAll('.logs-line').length === 50);
  assert.match(await page.locator('.logs-status').innerText(), /showing the last 50/);
  await page.locator('.logs-lines-choice select').selectOption('200');
  await page.waitForFunction(() => document.querySelectorAll('.logs-line').length === 200);

  // 8. Auto-refresh is a switch, off by default, and it reports its state.
  const live = page.locator('.logs-live .switch');
  assert.equal(await live.getAttribute('aria-checked'), 'false');
  await live.click();
  assert.equal(await live.getAttribute('aria-checked'), 'true');
  await live.click();
  assert.equal(await live.getAttribute('aria-checked'), 'false');

  // 9. Choosing another log switches the lines and the URL together.
  await page.locator('.logs-file', { hasText: 'notes.log' }).click();
  await page.waitForFunction(() => document.querySelectorAll('.logs-line').length === 2);
  assert.match(await page.evaluate(() => window.__location), /log=notes\.log/);
  assert.match(await page.locator('.logs-line').first().innerText(), /notes starting/);

  // 10. Download goes through the authenticated request, not a bare link.
  await page.getByRole('button', { name: 'Download' }).click();
  await page.waitForFunction(() => window.__downloadedLog === 'notes.log');

  // 11. Clearing asks first. Cancel leaves the log alone.
  await page.getByRole('button', { name: 'Clear' }).click();
  await page.getByRole('heading', { name: 'Clear notes.log?' }).waitFor();
  await shot('system-logs-clear');
  await page.getByRole('button', { name: 'Cancel' }).click();
  assert.equal(await page.evaluate(() => window.__clearedLog), undefined);
  assert.equal(await page.locator('.logs-line').count(), 2);

  // Confirming sends the request with its confirmation header and empties it.
  await page.getByRole('button', { name: 'Clear' }).click();
  await page.getByRole('button', { name: 'Clear log' }).click();
  await page.waitForFunction(() => window.__clearedLog === 'notes.log');
  await page.getByText('This log is empty.').waitFor();

  // 12. Overview reports what failed recently and links to the tab.
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.locator('.seg-opt', { hasText: 'Overview' }).click();
  await page.getByText('2 errors in the last 24 hours').waitFor();

  // Building a support bundle says plainly that nothing is sent anywhere.
  await page.getByText('sends it nowhere').waitFor();
  await page.getByRole('button', { name: 'Create support bundle' }).click();
  // The row in the list, not the toast that also names the file.
  await page.locator('.mini-list-name', { hasText: 'vela-support-20260916-094200.zip' }).waitFor();
  await shot('system-bundle');

  await page.getByRole('button', { name: 'See them' }).click();

  // 13. The Errors tab: one row per failure, with its repeat count, its source
  //     and its traceback behind a disclosure.
  await page.locator('.error-row').first().waitFor();
  assert.equal(await page.locator('.error-row').count(), 2);
  assert.deepEqual(await page.locator('.error-head .chip').allInnerTexts(), [
    'Engine',
    'Dashboard',
  ]);
  await page.getByText('×4').waitFor();
  assert.equal(await page.locator('.error-trace').count(), 0);
  await page.getByRole('button', { name: 'Show details' }).click();
  await page.locator('.error-trace').waitFor();
  assert.match(await page.locator('.error-trace').innerText(), /lifecycle\.py/);
  await shot('system-errors');

  // Search narrows the list; resolving moves a row out of Open.
  await page.getByRole('searchbox', { name: 'Search errors' }).fill('exited');
  await page.waitForFunction(() => document.querySelectorAll('.error-row').length === 1);
  await page.getByRole('searchbox', { name: 'Search errors' }).fill('');
  await page.waitForFunction(() => document.querySelectorAll('.error-row').length === 2);

  await page.locator('.error-row').first().getByRole('button', { name: 'Resolve' }).click();
  await page.waitForFunction(() => document.querySelectorAll('.error-row').length === 1);
  await page.locator('.seg-opt', { hasText: 'Resolved' }).click();
  await page.waitForFunction(() => document.querySelectorAll('.error-row').length === 1);
  await page.getByRole('button', { name: 'Reopen' }).waitFor();

  // Deleting removes it for good.
  await page.locator('.seg-opt', { hasText: 'All' }).click();
  await page.waitForFunction(() => document.querySelectorAll('.error-row').length === 2);
  await page.locator('.error-row').first().getByRole('button', { name: 'Delete' }).click();
  await page.waitForFunction(() => document.querySelectorAll('.error-row').length === 1);

  // 14. No sideways scrolling at a phone width, with the file list above the
  //     lines rather than beside them.
  await page.locator('.seg-opt', { hasText: 'Logs' }).click();
  await page.locator('.logs-layout').waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('.logs-file', { hasText: 'server.log' }).first().click();
  await page.locator('.logs-line').first().waitFor();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  assert.ok(overflow <= 1, `horizontal overflow at 390px: ${overflow}`);
  await shot('system-logs-390');

  assert.deepEqual(errors, [], `page errors: ${errors.join(', ')}`);
  console.log('System suite passed.');
} finally {
  await browser?.close();
  await server.close();
}
