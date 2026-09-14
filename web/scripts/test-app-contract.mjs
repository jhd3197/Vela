// Run after npm run build. Requires playwright (or NODE_PATH pointing to it).
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const python = process.env.VELA_TEST_PYTHON || path.join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const port = 17712;
const server = spawn(python, ['scripts/serve-contract-fixtures.py', '--port', String(port)], { cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
let serverOutput = '';
server.stdout.on('data', data => { serverOutput += data; });
server.stderr.on('data', data => { serverOutput += data; });
const base = `http://127.0.0.1:${port}`;
let browser;
try {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (server.exitCode !== null) throw new Error(serverOutput);
    try { if ((await fetch(`${base}/api/health`)).ok) break; } catch {}
    await new Promise(resolve => setTimeout(resolve, 100));
    if (attempt === 99) throw new Error(`Engine did not start: ${serverOutput}`);
  }
  browser = await chromium.launch({ headless: true, ...(process.env.VELA_BROWSER_CHANNEL ? { channel: process.env.VELA_BROWSER_CHANNEL } : {}) });
  const shots = path.join(root, 'docs/screenshots/increment-2');
  await fs.mkdir(shots, { recursive: true });
  for (const viewport of [{ width: 1366, height: 768 }, { width: 390, height: 844 }]) {
    const context = await browser.newContext({ viewport, isMobile: viewport.width < 500, hasTouch: viewport.width < 500 });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${base}/app/chat-fixture`);
    const frame = page.frameLocator('iframe');
    await frame.getByRole('textbox', { name: 'Message' }).waitFor();
    await page.waitForFunction(() => document.querySelector('iframe') && !document.querySelector('.appview-loading'));
    assert.equal(await page.locator('.appview-chrome, .sidebar, .topbar, .tabbar').count(), 0);
    assert.equal(await page.getByRole('button', { name: 'Vela app menu' }).isVisible(), true);
    await frame.getByRole('textbox', { name: 'Message' }).fill(`Saved from ${viewport.width}`);
    await frame.getByRole('button', { name: 'Send' }).click();
    await frame.getByText('Saved to your engine', { exact: true }).waitFor();
    await page.screenshot({ path: path.join(shots, `seamless-${viewport.width}.png`) });
    if (viewport.width < 500) {
      await page.setViewportSize({ width: 390, height: 460 });
      const composer = await frame.getByRole('button', { name: 'Send' }).boundingBox();
      assert.ok(composer.y + composer.height <= 460);
      assert.ok((await page.getByRole('button', { name: 'Vela app menu' }).boundingBox()).y >= 0);
      await page.setViewportSize(viewport);
    }
    const appFrame = page.frames().find(item => item.url().includes('/apps/chat-fixture/'));
    const boundary = await appFrame.evaluate(async () => {
      const result = { opaque: origin === 'null', hasToken: 'token' in Vela.context };
      try { parent.document.body; result.parentDenied = false; } catch { result.parentDenied = true; }
      try { localStorage.getItem('x'); result.localStorageDenied = false; } catch { result.localStorageDenied = true; }
      try { await fetch('/api/settings'); result.settingsDenied = false; } catch { result.settingsDenied = true; }
      result.reserved = Vela.context.viewport.hostControl;
      return result;
    });
    assert.equal(boundary.opaque, true); assert.equal(boundary.hasToken, false);
    assert.equal(boundary.parentDenied, true); assert.equal(boundary.localStorageDenied, true); assert.equal(boundary.settingsDenied, true);
    assert.ok(boundary.reserved.width >= 44 && boundary.reserved.x >= 0);
    await page.reload();
    await frame.getByText(`You wrote: Saved from ${viewport.width}`).waitFor();
    await frame.getByRole('textbox', { name: 'Message' }).fill('Unsaved draft');
    await page.getByRole('button', { name: 'Vela app menu' }).click();
    await page.getByRole('button', { name: 'Return to apps', exact: true }).click();
    await page.getByRole('dialog').waitFor();
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
    assert.equal(await frame.getByRole('textbox', { name: 'Message' }).inputValue(), 'Unsaved draft');
    await page.getByRole('button', { name: 'Vela app menu' }).click();
    await page.getByRole('button', { name: 'Return to apps', exact: true }).click();
    await page.getByRole('button', { name: 'Save and leave', exact: true }).click();
    await page.waitForURL(`${base}/apps`);
    await page.goto(`${base}/app/chat-fixture`);
    await page.waitForFunction(() => document.querySelector('iframe') && !document.querySelector('.appview-loading'));
    assert.equal(await frame.getByRole('textbox', { name: 'Message' }).inputValue(), 'Unsaved draft');
    await page.getByRole('button', { name: 'Vela app menu' }).click();
    await page.getByRole('button', { name: 'Show compact bar' }).click();
    assert.equal(await page.locator('.appview-chrome').isVisible(), true);
    await page.reload();
    await page.locator('.appview-chrome').waitFor();
    assert.equal(await page.locator('.appview-chrome').isVisible(), true);
    await page.getByRole('button', { name: 'Hide app bar' }).click();
    await page.goto(`${base}/app/other-app`);
    await page.waitForFunction(() => document.querySelector('iframe') && !document.querySelector('.appview-loading'));
    assert.equal(await page.frameLocator('iframe').getByText(`You wrote: Saved from ${viewport.width}`).count(), 0);
    for (const mode of ['compact', 'hub']) {
      await page.goto(`${base}/app/${mode}-fixture`);
      await page.locator('.appview-chrome').waitFor();
      await page.waitForFunction(() => document.querySelector('iframe') && !document.querySelector('.appview-loading'));
      assert.equal(await page.locator('.sidebar').count(), mode === 'hub' ? 1 : 0);
      if (mode === 'hub' && viewport.width < 500) {
        const composer = await page.frameLocator('iframe').getByRole('button', { name: 'Send' }).boundingBox();
        const tabs = await page.locator('.tabbar').boundingBox();
        assert.ok(composer.y + composer.height <= tabs.y, 'Hub navigation must not cover the composer');
      }
      await page.screenshot({ path: path.join(shots, `${mode}-${viewport.width}.png`) });
    }
    // Every hub navigation route participates in the unsaved-work guard.
    await page.frameLocator('iframe').getByRole('textbox', { name: 'Message' }).fill('Guard this draft');
    const appsLink = page.locator(viewport.width < 500 ? '.tabbar a[href="/apps"]' : '.sidebar a[href="/apps"]');
    await appsLink.click();
    await page.getByRole('dialog').waitFor();
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
    assert.ok(page.url().endsWith('/app/hub-fixture'));
    await appsLink.click();
    await page.getByRole('button', { name: 'Discard and leave', exact: true }).click();
    await page.waitForURL(`${base}/apps`);
    await page.getByRole('tab', { name: 'Running', exact: true }).click();
    await page.getByRole('button', { name: /Chat Fixture.*Running locally/ }).click();
    await page.getByRole('button', { name: 'Open', exact: true }).click();
    await page.frameLocator('iframe').getByRole('textbox', { name: 'Message' }).fill('Back navigation draft');
    await page.goBack();
    await page.getByRole('dialog').waitFor();
    await page.getByRole('button', { name: 'Discard and leave', exact: true }).click();
    await page.waitForURL(`${base}/apps`);
    assert.equal(await page.getByRole('tab', { name: 'Running', exact: true }).getAttribute('aria-selected'), 'true');
    await page.goto(`${base}/app/failed-fixture`);
    await page.getByText('Couldn’t open the app', { exact: true }).waitFor({ timeout: 15000 });
    await page.screenshot({ path: path.join(shots, `failed-${viewport.width}.png`) });
    const exit = page.getByRole('button', { name: 'Vela app menu' });
    await exit.focus(); await page.keyboard.press('Enter');
    await page.getByRole('button', { name: 'Close app view' }).click();
    await page.waitForURL(`${base}/apps`);
    assert.deepEqual(errors, []);
    console.log(`PASS ${viewport.width}x${viewport.height}: persistence, opaque boundary, all chrome modes, draft save/cancel, compact preference, failed-load keyboard exit`);
    await context.close();
  }
} finally {
  await browser?.close();
  server.kill();
}
