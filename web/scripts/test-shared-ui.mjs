import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

const web = fileURLToPath(new URL('..', import.meta.url));
const server = await createServer({
  configFile: false, root: web, plugins: [react()],
  css: { preprocessorOptions: { scss: { api: 'modern' } } },
  server: { host: '127.0.0.1', port: 0 },
});
let browser;
try {
  await server.listen();
  const { port } = server.httpServer.address();
  browser = await chromium.launch({ headless: true, channel: process.env.VELA_BROWSER_CHANNEL || 'chrome' });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${port}/scripts/fixtures/shared-ui.html`);
  await page.waitForFunction(() => window.fixture?.reads.length === 1);

  await page.getByRole('button', { name: 'Ordinary button', exact: true }).click();
  assert.equal(await page.evaluate(() => fixture.submissions), 0);
  await page.getByRole('button', { name: 'Submit form', exact: true }).click();
  assert.equal(await page.evaluate(() => fixture.submissions), 1);
  const field = page.getByLabel('Server', { exact: true });
  assert.equal(await field.getAttribute('aria-invalid'), 'true');
  const help = await field.getAttribute('aria-describedby');
  assert.equal(help.split(' ').length, 3);
  for (const id of help.split(' ')) assert.equal(await page.evaluate(id => Boolean(document.getElementById(id)), id), true);
  assert.notEqual(await field.getAttribute('id'), await page.getByLabel('Topic', { exact: true }).getAttribute('id'));

  // The first response arrives after switching IDs. It must never replace the second.
  await page.getByRole('button', { name: 'Switch resource', exact: true }).click();
  await page.waitForFunction(() => fixture.reads.length === 2);
  assert.equal(await page.evaluate(() => fixture.reads[0].signal.aborted), true);
  await page.evaluate(() => fixture.reads[1].resolve('second app'));
  await page.getByTestId('resource').filter({ hasText: 'second app' }).waitFor();
  await page.evaluate(() => fixture.reads[0].resolve('stale first app'));
  assert.equal(await page.getByTestId('resource').textContent(), 'second app');

  // Refresh joins an in-flight read, reports errors, then recovers on retry.
  await page.evaluate(() => { fixture.refresh(); fixture.refresh(); });
  await page.waitForFunction(() => fixture.reads.length === 3);
  await page.evaluate(() => fixture.reads[2].reject(new Error('offline')));
  await page.getByTestId('resource-error').filter({ hasText: 'offline' }).waitFor();
  assert.equal(await page.getByTestId('resource').textContent(), 'second app');
  await page.evaluate(() => { fixture.refresh(); });
  await page.waitForFunction(() => fixture.reads.length === 4);
  await page.evaluate(() => fixture.reads[3].resolve('recovered'));
  await page.getByTestId('resource').filter({ hasText: 'recovered' }).waitFor();
  assert.equal(await page.getByTestId('resource-error').textContent(), '');
  await page.getByRole('button', { name: 'Toggle resource', exact: true }).click();
  assert.equal(await page.getByTestId('resource').textContent(), 'empty');
  await page.getByRole('button', { name: 'Toggle resource', exact: true }).click();
  await page.waitForFunction(() => fixture.reads.length === 5);
  await page.getByRole('status').filter({ hasText: 'Loading resource' }).waitFor();
  assert.equal(await page.getByTestId('resource').textContent(), 'empty');

  // Two submissions within one render still start only one operation.
  await page.evaluate(() => { fixture.submit(); fixture.submit(); });
  await page.waitForFunction(() => fixture.actions.length === 1);
  assert.equal(await page.getByRole('button', { name: 'Run action', exact: true }).isDisabled(), true);
  assert.equal(await page.getByRole('button', { name: 'Run action', exact: true }).getAttribute('aria-busy'), 'true');
  await page.evaluate(() => fixture.actions[0].reject(new Error('Action failed')));
  await page.getByRole('alert').filter({ hasText: 'Action failed' }).waitFor();
  await page.getByRole('button', { name: 'Run action', exact: true }).click();
  await page.waitForFunction(() => fixture.actions.length === 2);
  assert.equal(await page.getByRole('alert').filter({ hasText: 'Action failed' }).count(), 0);
  await page.evaluate(() => fixture.actions[1].resolve('saved'));
  await page.waitForFunction(() => fixture.completions === 1);

  await page.getByRole('button', { name: 'Run action', exact: true }).click();
  await page.waitForFunction(() => fixture.actions.length === 3);
  await page.getByRole('button', { name: 'Toggle action', exact: true }).click();
  await page.getByRole('button', { name: 'Toggle action', exact: true }).click();
  await page.evaluate(() => fixture.actions[2].resolve('old action'));
  assert.equal(await page.evaluate(() => fixture.completions), 1);
  assert.equal(await page.getByRole('button', { name: 'Run action', exact: true }).isEnabled(), true);
  assert.deepEqual(errors, []);
  console.log('PASS: native form behavior, accessible fields, strict-mode cleanup, stale reads, retry, duplicate actions and unmount safety');
} finally {
  await browser?.close();
  await server.close();
}
