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
const python =
  process.env.VELA_TEST_PYTHON ||
  path.join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const port = 17712;
const server = spawn(python, ['scripts/serve-contract-fixtures.py', '--port', String(port)], {
  cwd: root,
  windowsHide: true,
  stdio: ['ignore', 'pipe', 'pipe'],
});
let serverOutput = '';
server.stdout.on('data', (data) => {
  serverOutput += data;
});
server.stderr.on('data', (data) => {
  serverOutput += data;
});
const base = `http://127.0.0.1:${port}`;
let browser;
try {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (server.exitCode !== null) throw new Error(serverOutput);
    try {
      if ((await fetch(`${base}/api/health`)).ok) break;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
    if (attempt === 99) throw new Error(`Engine did not start: ${serverOutput}`);
  }
  browser = await chromium.launch({
    headless: true,
    ...(process.env.VELA_BROWSER_CHANNEL ? { channel: process.env.VELA_BROWSER_CHANNEL } : {}),
  });
  const shots = path.join(root, 'docs/screenshots/increment-2');
  await fs.mkdir(shots, { recursive: true });
  for (const viewport of [
    { width: 1366, height: 768 },
    { width: 390, height: 844 },
  ]) {
    const context = await browser.newContext({
      viewport,
      isMobile: viewport.width < 500,
      hasTouch: viewport.width < 500,
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(`${base}/app/chat-fixture`);
    const frame = page.frameLocator('iframe');
    await frame.getByRole('textbox', { name: 'Message' }).waitFor();
    await page.waitForFunction(
      () => document.querySelector('iframe') && !document.querySelector('.appview-loading'),
    );
    assert.equal(await page.locator('.appview-chrome, .rail, .app-titlebar').count(), 0);
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
    const appFrame = page.frames().find((item) => item.url().includes('/apps/chat-fixture/'));
    const boundary = await appFrame.evaluate(async () => {
      const result = { opaque: origin === 'null', hasToken: 'token' in Vela.context };
      try {
        parent.document.body;
        result.parentDenied = false;
      } catch {
        result.parentDenied = true;
      }
      try {
        localStorage.getItem('x');
        result.localStorageDenied = false;
      } catch {
        result.localStorageDenied = true;
      }
      try {
        await fetch('/api/settings');
        result.settingsDenied = false;
      } catch {
        result.settingsDenied = true;
      }
      result.reserved = Vela.context.viewport.hostControl;
      return result;
    });
    assert.equal(boundary.opaque, true);
    assert.equal(boundary.hasToken, false);
    assert.equal(boundary.parentDenied, true);
    assert.equal(boundary.localStorageDenied, true);
    assert.equal(boundary.settingsDenied, true);
    assert.ok(boundary.reserved.width >= 44 && boundary.reserved.x >= 0);
    await page.reload();
    await frame.getByText(`You wrote: Saved from ${viewport.width}`).waitFor();
    await frame.getByRole('textbox', { name: 'Message' }).fill('Unsaved draft');
    await page.getByRole('button', { name: 'Vela app menu' }).click();
    await page.getByRole('button', { name: 'Return to apps', exact: true }).click();
    await page.getByRole('dialog').waitFor();
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
    assert.equal(
      await frame.getByRole('textbox', { name: 'Message' }).inputValue(),
      'Unsaved draft',
    );
    await page.getByRole('button', { name: 'Vela app menu' }).click();
    await page.getByRole('button', { name: 'Return to apps', exact: true }).click();
    await page.getByRole('button', { name: 'Save and leave', exact: true }).click();
    await page.waitForURL(`${base}/apps`);
    await page.goto(`${base}/app/chat-fixture`);
    await page.waitForFunction(
      () => document.querySelector('iframe') && !document.querySelector('.appview-loading'),
    );
    assert.equal(
      await frame.getByRole('textbox', { name: 'Message' }).inputValue(),
      'Unsaved draft',
    );
    await page.getByRole('button', { name: 'Vela app menu' }).click();
    await page.getByRole('button', { name: 'Show compact bar' }).click();
    // The compact bar is the app's own title bar beside the rail, not a second
    // navigation. It carries the app menu, and the rail stays on screen.
    assert.equal(await page.locator('.app-titlebar').isVisible(), true);
    assert.equal(await page.locator('.rail').count(), 1);
    await page.reload();
    await page.locator('.app-titlebar').waitFor();
    assert.equal(await page.locator('.app-titlebar').isVisible(), true);
    // Hide the bar again from the app-window menu, returning to seamless.
    await page.getByRole('button', { name: 'App menu' }).click();
    await page.getByRole('menuitem', { name: 'Hide app bar' }).click();
    await page.getByRole('button', { name: 'Vela app menu' }).waitFor();
    assert.equal(await page.locator('.app-titlebar').count(), 0);
    await page.goto(`${base}/app/other-app`);
    await page.waitForFunction(
      () => document.querySelector('iframe') && !document.querySelector('.appview-loading'),
    );
    assert.equal(
      await page
        .frameLocator('iframe')
        .getByText(`You wrote: Saved from ${viewport.width}`)
        .count(),
      0,
    );
    for (const mode of ['compact', 'hub']) {
      await page.goto(`${base}/app/${mode}-fixture`);
      // Compact and hub both give the app its own title bar beside the rail.
      await page.locator('.app-titlebar').waitFor();
      await page.waitForFunction(
        () => document.querySelector('iframe') && !document.querySelector('.appview-loading'),
      );
      assert.equal(await page.locator('.rail').count(), 1);
      // No duplicate navigation: one title bar, and no second hub header.
      assert.equal(await page.locator('.app-titlebar').count(), 1);
      assert.equal(await page.locator('.workspace-header').count(), 0);
      assert.equal(await page.locator(`.appview-${mode}.appview-hosted`).count(), 1);
      if (viewport.width < 500) {
        const composer = await page
          .frameLocator('iframe')
          .getByRole('button', { name: 'Send' })
          .boundingBox();
        const height = await page.evaluate(() => innerHeight);
        assert.ok(composer.y + composer.height <= height, 'The composer must fit on a phone');
      }
      // The bridge reports the frame's real box, so the rail and contextual
      // header are outside the app's coordinate space.
      const modeFrame = page.frames().find((item) => item.url().includes(`/apps/${mode}-fixture/`));
      const reported = await modeFrame.evaluate(() => ({
        width: Vela.context.viewport.width,
        height: Vela.context.viewport.height,
        chrome: Vela.context.view.chrome,
        theme: Vela.context.theme,
      }));
      const actual = await page
        .locator('iframe')
        .evaluate((frame) => ({ width: frame.clientWidth, height: frame.clientHeight }));
      assert.equal(reported.chrome, mode);
      assert.equal(
        reported.theme,
        await page.evaluate(() => document.documentElement.dataset.theme),
      );
      assert.ok(Math.abs(reported.width - actual.width) <= 1, JSON.stringify({ reported, actual }));
      assert.ok(
        Math.abs(reported.height - actual.height) <= 1,
        JSON.stringify({ reported, actual }),
      );
      // A hub app keeps the rail at every width, so its frame is always the
      // window minus that column rather than the whole window.
      if (mode === 'hub') {
        const rail = await page.locator('.rail').evaluate((el) => el.getBoundingClientRect().width);
        assert.ok(rail >= 44, `the hub rail is ${rail}px at ${viewport.width}px`);
        assert.ok(
          actual.width <= viewport.width - rail,
          'the hub frame sits beside the rail rather than under it',
        );
      }
      await page.screenshot({ path: path.join(shots, `${mode}-${viewport.width}.png`) });
    }
    // Polling updates the header, not the app: the iframe must not remount.
    await page.evaluate(() => (document.querySelector('iframe').dataset.marker = 'kept'));
    await page.waitForTimeout(6500);
    assert.equal(
      await page.evaluate(() => document.querySelector('iframe').dataset.marker),
      'kept',
      'app polling must not remount the workspace iframe',
    );

    // The title bar names the app, shows its state and a way back, and carries
    // no search field of its own (the palette is still Ctrl+K).
    await page.goto(`${base}/app/compact-fixture`);
    await page.locator('.app-titlebar').waitFor();
    assert.equal(
      await page.locator('.app-titlebar').getByRole('button', { name: 'Back' }).count(),
      1,
    );
    // The state pill appears once the app record settles.
    await page.locator('.app-pill').first().waitFor();
    assert.equal(await page.locator('.app-titlebar').getByRole('searchbox').count(), 0);
    // The window menu can pin the app to the rail.
    await page.getByRole('button', { name: 'App menu' }).click();
    assert.equal(await page.getByRole('menuitem', { name: 'Pin to rail' }).count(), 1);
    await page.keyboard.press('Escape');

    // A missing app is a clear dead end that leads back to the Marketplace.
    await page.goto(`${base}/app/no-such-app`);
    await page.getByRole('heading', { name: 'App unavailable' }).waitFor();
    assert.equal(await page.locator('.appview-interstitial a[href="/library"]').count(), 1);

    // Back on a hub app: every hub navigation route participates in the
    // unsaved-work guard.
    await page.goto(`${base}/app/hub-fixture`);
    await page.waitForFunction(
      () => document.querySelector('iframe') && !document.querySelector('.appview-loading'),
    );
    await page
      .frameLocator('iframe')
      .getByRole('textbox', { name: 'Message' })
      .fill('Guard this draft');
    // The Launchpad is a rail destination now. A hub app keeps the rail on
    // screen at every width, so leaving through the Launchpad link is a
    // navigation the unsaved-work guard covers on a phone and a desktop alike.
    const openLaunchpad = async () => {
      await page.locator('.rail a[href="/apps"]').click();
    };
    await openLaunchpad();
    await page.getByRole('dialog').waitFor();
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
    assert.ok(page.url().endsWith('/app/hub-fixture'));
    if (viewport.width < 500) {
      // The rail a hub app keeps on a phone is a navigation control like any
      // other: leaving through it asks about the unsaved draft first.
      assert.equal(await page.getByRole('button', { name: 'Open navigation' }).count(), 0);
      await page.locator('.rail a[href="/library"]').click();
      await page.getByRole('button', { name: 'Cancel', exact: true }).click();
      assert.ok(page.url().endsWith('/app/hub-fixture'));
      assert.equal(
        await page.frameLocator('iframe').getByRole('textbox', { name: 'Message' }).inputValue(),
        'Guard this draft',
      );
    }
    await openLaunchpad();
    await page.getByRole('button', { name: 'Discard and leave', exact: true }).click();
    await page.waitForURL(`${base}/apps`);
    // Reopen the running app from the Launchpad to check the back-button guard.
    await page.locator('.launchpad').waitFor();
    await page.locator('.launch-tile', { hasText: 'Chat Fixture' }).first().click();
    await page.waitForURL(`${base}/app/chat-fixture`);
    await page
      .frameLocator('iframe')
      .getByRole('textbox', { name: 'Message' })
      .fill('Back navigation draft');
    await page.goBack();
    await page.getByRole('dialog').waitFor();
    await page.getByRole('button', { name: 'Discard and leave', exact: true }).click();
    await page.waitForURL(`${base}/apps`);
    await page.locator('.launchpad').waitFor();
    await page.goto(`${base}/app/failed-fixture`);
    await page.getByText('Couldn’t open the app', { exact: true }).waitFor({ timeout: 15000 });
    await page.screenshot({ path: path.join(shots, `failed-${viewport.width}.png`) });
    const exit = page.getByRole('button', { name: 'Vela app menu' });
    await exit.focus();
    await page.keyboard.press('Enter');
    await page.getByRole('button', { name: 'Close app view' }).click();
    await page.waitForURL(`${base}/apps`);
    assert.deepEqual(errors, []);
    console.log(
      `PASS ${viewport.width}x${viewport.height}: persistence, opaque boundary, all chrome modes, draft save/cancel, compact preference, failed-load keyboard exit`,
    );
    await context.close();
  }
} finally {
  await browser?.close();
  server.kill();
}
