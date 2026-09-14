// Disposable HTTPS acceptance: separate desktop/phone browser stores, real SDK bridge.
import { createRequire } from 'node:module';
import { spawn, execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs/promises';
import os from 'node:os';
import https from 'node:https';
import { createPublicKey, createHash } from 'node:crypto';
import assert from 'node:assert/strict';
const { chromium } = createRequire(import.meta.url)('playwright');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'vela-tls-'));
const cert = path.join(temp, 'cert.pem'),
  key = path.join(temp, 'key.pem');
execFileSync(
  'openssl',
  [
    'req',
    '-x509',
    '-newkey',
    'rsa:2048',
    '-nodes',
    '-keyout',
    key,
    '-out',
    cert,
    '-days',
    '1',
    '-subj',
    '/CN=127.0.0.1',
    '-addext',
    'subjectAltName=IP:127.0.0.1',
  ],
  { windowsHide: true, stdio: 'ignore' },
);
const pem = await fs.readFile(cert);
const pin = createHash('sha256')
  .update(createPublicKey(pem).export({ type: 'spki', format: 'der' }))
  .digest('base64');
const python =
  process.env.VELA_TEST_PYTHON ||
  path.join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const server = spawn(
  python,
  ['scripts/serve-connection-fixtures.py', '--cert', cert, '--key', key],
  { cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] },
);
let output = '',
  browser;
server.stderr.on('data', (chunk) => (output += chunk));
const base = 'https://127.0.0.1:17713';
try {
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw Error(output);
    try {
      await new Promise((resolve, reject) =>
        https
          .get(base + '/api/health', { ca: pem }, (r) => {
            r.resume();
            r.on('end', resolve);
          })
          .on('error', reject),
      );
      break;
    } catch {
      if (i === 99) throw Error(output);
      await new Promise((r) => setTimeout(r, 100));
    }
  }
  browser = await chromium.launch({
    headless: true,
    channel: process.env.VELA_BROWSER_CHANNEL || 'chrome',
    args: [`--ignore-certificate-errors-spki-list=${pin}`],
  });
  const pages = [];
  for (const viewport of [
    { width: 1366, height: 900 },
    { width: 390, height: 844 },
  ]) {
    const context = await browser.newContext({
      viewport,
      isMobile: viewport.width < 500,
      hasTouch: viewport.width < 500,
    });
    const page = await context.newPage();
    pages.push(page);
    await page.goto(base + '/app/health');
    await page.getByLabel('Password', { exact: true }).fill('fixture-password-123');
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.frameLocator('iframe').getByRole('tab', { name: 'Habits', exact: true }).click();
    await page.frameLocator('iframe').locator('#habitName').waitFor();
  }
  const [a, b] = pages,
    fa = a.frameLocator('iframe'),
    fb = b.frameLocator('iframe');
  const legacy = [
    { id: 'legacy-one', name: 'Imported habit', target: 3, color: '#aabbcc', days: {} },
  ];
  await a.evaluate(
    (value) => localStorage.setItem('vela.health.habits.v1', JSON.stringify(value)),
    legacy,
  );
  await a.getByText('Import earlier Health data', { exact: true }).click();
  await a.getByRole('button', { name: 'Use this browser’s earlier data' }).click();
  await a.getByRole('button', { name: 'Review import', exact: true }).click();
  await a.getByRole('button', { name: 'Import and keep both versions' }).click();
  await a.getByText('Import verified.', { exact: false }).waitFor();
  assert.deepEqual(
    JSON.parse(await a.evaluate(() => localStorage.getItem('vela.health.habits.v1'))),
    legacy,
  );
  await a.getByText('Import earlier Health data', { exact: true }).click();
  await fa.getByText('Imported habit', { exact: true }).waitFor();
  await fb.getByText('Imported habit', { exact: true }).waitFor();
  const add = async (frame, name) => {
    await frame.locator('#habitName').fill(name);
    await frame.locator('#addHabit').click();
    await frame.locator('#syncStatus').filter({ hasText: 'Saved to your engine' }).waitFor();
  };
  await add(fa, 'Desktop habit');
  await fb.getByText('Desktop habit', { exact: true }).waitFor();
  // A dirty draft pauses polling. B wins the next revision; A must retain its edits.
  await fa.locator('#habitName').fill('Conflicting draft');
  await add(fb, 'Phone habit');
  await fa.locator('#addHabit').click();
  await fa.locator('#syncStatus').filter({ hasText: 'Another client changed' }).waitFor();
  await fa.locator('#copyDraft').click();
  assert.match(await fa.locator('#recoveryJson').inputValue(), /Conflicting draft/);
  await fa.locator('#reloadData').click();
  await fa.getByText('Phone habit', { exact: true }).waitFor();
  await fa.locator('#makeBackup').click();
  await fa.locator('#syncStatus').filter({ hasText: 'Backup created' }).waitFor();
  await add(fb, 'After backup');
  await fa.getByText('After backup', { exact: true }).waitFor();
  await fa.locator('#restoreBackup').click();
  await fa.locator('#confirmRestore').click();
  await fa.locator('#syncStatus').filter({ hasText: 'Backup restored' }).waitFor();
  await fb.getByText('After backup', { exact: true }).waitFor({ state: 'hidden' });
  const shots = path.join(root, 'docs/screenshots/connections');
  await fs.mkdir(shots, { recursive: true });
  for (const page of pages)
    await page.screenshot({ path: path.join(shots, `health-${page.viewportSize().width}.png`) });
  await b.reload(); // secure cookie restores only this client's session.
  await b.frameLocator('iframe').locator('#habitName').waitFor({ state: 'attached' });
  await a.goto(base + '/app/ollama');
  await a.getByRole('button', { name: 'Test and connect', exact: true }).click();
  await a.frameLocator('iframe').getByText('fixture:latest', { exact: true }).waitFor();
  await a
    .frameLocator('iframe')
    .getByRole('button', { name: 'View details for fixture:latest' })
    .click();
  await a.frameLocator('iframe').locator('#modelInfo').filter({ hasText: 'fixture' }).waitFor();
  await a.screenshot({ path: path.join(shots, 'ollama.png') });
  console.log(
    'PASS: authenticated TLS, independent desktop/mobile clients, retained migration, cross-client updates, conflict recovery, backup/restore, cookie resume, Ollama read bridge',
  );
} finally {
  await browser?.close();
  server.kill();
  await new Promise((resolve) =>
    server.exitCode !== null ? resolve() : server.once('exit', resolve),
  );
  await fs.rm(temp, { recursive: true, force: true });
}
