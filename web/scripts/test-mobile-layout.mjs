import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

// The shared mobile rules — touch text size, gesture policy, scroll ownership
// and the viewport service's geometry — checked in a real browser against an
// isolated fixture. It never contacts a Vela server or a user's installed apps.
//
// A headless browser has no on-screen keyboard: the fixture drives a stand-in
// visual viewport so the host's own measurement, variables and layout run for
// real. That catches a regression in this code; it is not a substitute for
// accepting keyboard behaviour on a physical iPhone and Android phone.
const web = fileURLToPath(new URL('..', import.meta.url));
const shots = fileURLToPath(new URL('../../docs/screenshots/mobile', import.meta.url));
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
  const base = `http://127.0.0.1:${port}/scripts/fixtures/mobile.html`;
  browser = await chromium.launch({
    headless: true,
    channel: process.env.VELA_BROWSER_CHANNEL || 'chrome',
  });
  // A phone-sized window that reports a coarse pointer, as a touch device does.
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    hasTouch: true,
    isMobile: true,
    deviceScaleFactor: 3,
  });
  const page = await context.newPage();
  await page.addInitScript(() => localStorage.setItem('vela.welcome.v1', 'done'));
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await fs.mkdir(shots, { recursive: true });
  const shot = async (name) => page.screenshot({ path: path.join(shots, `${name}.png`) });
  const still = () =>
    page.addStyleTag({
      content:
        '*, *::before, *::after { animation: none !important; transition: none !important; }',
    });
  const variables = () =>
    page.evaluate(() => {
      const style = getComputedStyle(document.documentElement);
      return {
        height: style.getPropertyValue('--vela-visible-height').trim(),
        top: style.getPropertyValue('--vela-visible-top').trim(),
        keyboard: style.getPropertyValue('--vela-keyboard-inset').trim(),
      };
    });
  const noOverflow = async (label) => {
    const overflow = await page.evaluate(() => ({
      body: document.documentElement.scrollWidth - innerWidth,
      widest: [...document.querySelectorAll('.workspace-content, .chat-log, .modal-dialog')].map(
        (el) => el.scrollWidth - el.clientWidth,
      ),
    }));
    assert.ok(
      overflow.body <= 1 && overflow.widest.every((value) => value <= 1),
      `${label}: ${JSON.stringify(overflow)}`,
    );
  };

  // ---- Touch text size -----------------------------------------------------
  // Every field a finger can reach renders at 16 CSS pixels or more, so iOS
  // does not zoom the page on focus and leave it zoomed.
  await page.goto(`${base}?at=/forms`);
  await page.locator('[data-testid="text-field"]').waitFor();
  await still();
  const smallFields = await page.evaluate(() => {
    const typed = new Set([
      'button',
      'submit',
      'reset',
      'checkbox',
      'radio',
      'range',
      'color',
      'file',
      'image',
    ]);
    return [...document.querySelectorAll('input, textarea, select')]
      .filter((el) => el.tagName !== 'INPUT' || !typed.has((el.type || 'text').toLowerCase()))
      .filter((el) => parseFloat(getComputedStyle(el).fontSize) < 16)
      .map((el) => `${el.tagName}.${el.className}:${getComputedStyle(el).fontSize}`);
  });
  assert.deepEqual(smallFields, [], 'a touch field below 16px triggers focus zoom on iOS');

  // The rule is a floor, not a cap: a reader with larger text keeps it.
  const enlarged = await page.evaluate(() => {
    const field = document.querySelector('[data-testid="text-field"]');
    field.parentElement.style.fontSize = '22px';
    const size = getComputedStyle(field).fontSize;
    field.parentElement.style.fontSize = '';
    return size;
  });
  assert.equal(enlarged, '22px');

  // ---- Gestures ------------------------------------------------------------
  // Pinch zoom, panning and selection stay available; only the double-tap delay
  // is taken off ordinary controls.
  const gestures = await page.evaluate(() => ({
    viewport: document.querySelector('meta[name="viewport"]').content,
    button: getComputedStyle(document.querySelector('.btn')).touchAction,
    body: getComputedStyle(document.body).touchAction,
    selection: getComputedStyle(document.body).userSelect,
  }));
  assert.ok(!/user-scalable\s*=\s*no|maximum-scale/.test(gestures.viewport), gestures.viewport);
  assert.equal(gestures.button, 'manipulation');
  assert.equal(gestures.body, 'auto', 'the page itself must still pan and pinch');
  assert.notEqual(gestures.selection, 'none', 'text stays selectable and copyable');

  // ---- Scroll ownership ----------------------------------------------------
  const scrollOwners = await page.evaluate(() => {
    const content = document.querySelector('.workspace-content');
    const style = getComputedStyle(content);
    return {
      overflowY: style.overflowY,
      chaining: style.overscrollBehaviorY,
      // Containing both axes would also block the browser's horizontal
      // back gesture, so the horizontal axis is deliberately left alone.
      horizontal: style.overscrollBehaviorX,
      scrolls: content.scrollHeight > content.clientHeight,
    };
  });
  assert.deepEqual(scrollOwners, {
    overflowY: 'auto',
    chaining: 'contain',
    horizontal: 'auto',
    scrolls: true,
  });
  await noOverflow('settings at 390px');

  // ---- A dialog body scrolls while the page behind it stays put ------------
  const content = page.locator('.workspace-content');
  // The web font swapping in after the scroll position is set would reflow the
  // column and move the anchor; settle it first so only the dialog is measured.
  await page.evaluate(() => document.fonts.ready);
  await content.evaluate((el) => (el.scrollTop = 400));
  // Dispatched rather than clicked: Playwright would scroll the control back
  // into view first, and the position the page keeps is what is being checked.
  await page.getByRole('button', { name: 'Open form dialog' }).dispatchEvent('click');
  const dialog = page.getByRole('dialog', { name: 'Form dialog' });
  await dialog.waitFor();
  const locked = await page.evaluate(() => ({
    documentOverflow: document.documentElement.style.overflow,
    behind: document.querySelector('.workspace-content').scrollTop,
  }));
  assert.equal(locked.documentOverflow, 'hidden');
  assert.equal(locked.behind, 400, 'the page behind the dialog keeps its position');
  const dialogFit = await dialog.evaluate((el) => ({
    scrolls: el.scrollHeight > el.clientHeight,
    bottom: el.getBoundingClientRect().bottom,
    height: innerHeight,
  }));
  assert.ok(dialogFit.scrolls, 'a long dialog body scrolls inside the dialog');
  assert.ok(dialogFit.bottom <= dialogFit.height + 1, JSON.stringify(dialogFit));
  await shot('dialog-phone');

  // ---- A keyboard under the dialog ----------------------------------------
  // The keyboard is only ever inferred with a text entry focused, so focus one.
  await page.locator('[data-testid="dialog-field"]').focus();
  await page.evaluate(() => window.fixture.keyboard(336));
  await page.waitForFunction(
    () =>
      getComputedStyle(document.documentElement)
        .getPropertyValue('--vela-keyboard-inset')
        .trim() !== '0px',
  );
  assert.deepEqual(await variables(), { height: '508px', top: '0px', keyboard: '336px' });
  const raised = await dialog.evaluate((el) => {
    const box = el.getBoundingClientRect();
    return { bottom: box.bottom, top: box.top, visible: innerHeight - 336 };
  });
  assert.ok(
    raised.bottom <= raised.visible + 1,
    `the dialog must clear the keyboard: ${JSON.stringify(raised)}`,
  );
  assert.ok(raised.top >= 0, JSON.stringify(raised));
  await shot('dialog-keyboard');
  await page.evaluate(() => window.fixture.reset());
  await page.waitForFunction(
    () =>
      getComputedStyle(document.documentElement)
        .getPropertyValue('--vela-keyboard-inset')
        .trim() === '0px',
  );
  await page.keyboard.press('Escape');
  await dialog.waitFor({ state: 'detached' });
  assert.equal(
    await page.evaluate(() => document.documentElement.style.overflow),
    '',
    'dismissing the dialog gives scrolling back',
  );

  // ---- A drawer keeps its own body scrolling and its actions reachable -----
  await page.getByRole('button', { name: 'Open drawer' }).click();
  const drawer = page.getByRole('dialog', { name: 'Details drawer' });
  await drawer.waitFor();
  await still();
  const drawerFit = await page.evaluate(() => {
    const body = document.querySelector('[data-testid="drawer-body"]');
    const close = document.querySelector('.drawer-footer .btn').getBoundingClientRect();
    return {
      scrolls: body.scrollHeight > body.clientHeight,
      chaining: getComputedStyle(body).overscrollBehaviorY,
      actionBottom: close.bottom,
      height: innerHeight,
    };
  });
  assert.equal(drawerFit.scrolls, true);
  assert.equal(drawerFit.chaining, 'contain');
  assert.ok(drawerFit.actionBottom <= drawerFit.height + 1, JSON.stringify(drawerFit));
  await page.keyboard.press('Escape');
  await drawer.waitFor({ state: 'detached' });

  // ---- The conversation pattern under a keyboard ---------------------------
  await page.goto(`${base}?at=/conversation`);
  await page.locator('[data-testid="transcript"]').waitFor();
  await still();
  const transcript = page.locator('[data-testid="transcript"]');
  await transcript.evaluate((el) => (el.scrollTop = 300));
  const composer = page.locator('.chat-composer textarea');
  await composer.focus();
  const restBox = await composer.evaluate((el) => el.getBoundingClientRect().bottom);
  assert.ok(restBox <= 844 + 1, `composer at rest: ${restBox}`);
  await shot('conversation-phone');

  await page.evaluate(() => window.fixture.keyboard(336));
  await page.waitForFunction(
    () =>
      getComputedStyle(document.documentElement)
        .getPropertyValue('--vela-keyboard-inset')
        .trim() === '336px',
  );
  const withKeyboard = await page.evaluate(() => {
    const field = document.querySelector('.chat-composer textarea').getBoundingClientRect();
    const log = document.querySelector('[data-testid="transcript"]');
    return {
      composerBottom: field.bottom,
      composerHeight: field.height,
      visible: innerHeight - 336,
      transcriptTop: log.getBoundingClientRect().top,
      scrollTop: log.scrollTop,
      transcriptHeight: log.clientHeight,
    };
  });
  assert.ok(
    withKeyboard.composerBottom <= withKeyboard.visible + 1,
    `the composer must stay above the keyboard: ${JSON.stringify(withKeyboard)}`,
  );
  assert.ok(withKeyboard.composerHeight >= 36, JSON.stringify(withKeyboard));
  assert.equal(withKeyboard.scrollTop, 300, 'the reader is not jumped away from the messages');
  assert.ok(withKeyboard.transcriptHeight > 0, JSON.stringify(withKeyboard));
  await shot('conversation-keyboard');

  // The transcript gave up the space, not the composer.
  await page.evaluate(() => window.fixture.reset());
  await page.waitForFunction(
    () =>
      getComputedStyle(document.documentElement)
        .getPropertyValue('--vela-keyboard-inset')
        .trim() === '0px',
  );
  const restored = await page.evaluate(() => {
    const log = document.querySelector('[data-testid="transcript"]');
    return { height: log.clientHeight, scrollTop: log.scrollTop };
  });
  assert.ok(restored.height > withKeyboard.transcriptHeight, JSON.stringify(restored));
  assert.equal(restored.scrollTop, 300, 'closing the keyboard keeps the reading position');

  // ---- Pinch zoom is not a keyboard ---------------------------------------
  // The visible rectangle halves, exactly as it does when a keyboard opens.
  // Nothing may reflow: the reader is magnifying the page, not losing space.
  const beforeZoom = await page.evaluate(() => ({
    composer: document.querySelector('.chat-composer').getBoundingClientRect().height,
    transcript: document.querySelector('[data-testid="transcript"]').clientHeight,
  }));
  // Still focused in the composer: a smaller rectangle now is the reader
  // magnifying the page, and must not be read as a keyboard.
  await page.evaluate(() => window.fixture.zoom(2.4, 180));
  await page.waitForFunction(() => window.visualViewport.scale > 1);
  await page.waitForTimeout(50);
  assert.deepEqual(await variables(), { height: '844px', top: '0px', keyboard: '0px' });
  const duringZoom = await page.evaluate(() => ({
    composer: document.querySelector('.chat-composer').getBoundingClientRect().height,
    transcript: document.querySelector('[data-testid="transcript"]').clientHeight,
  }));
  assert.deepEqual(duringZoom, beforeZoom, 'a zoomed page keeps its layout and can be panned');
  await page.evaluate(() => window.fixture.reset());

  // ---- Every page keeps its navigation, at every width ----------------------
  for (const size of [
    { width: 320, height: 640, label: 'narrow phone' },
    { width: 390, height: 844, label: 'phone' },
    { width: 740, height: 360, label: 'short landscape' },
    { width: 834, height: 1112, label: 'tablet split screen' },
  ]) {
    await page.setViewportSize({ width: size.width, height: size.height });
    await page.goto(base);
    await page.locator('.rail-apps a').first().waitFor();
    await still();
    assert.equal(await page.locator('.rail').isVisible(), true, `${size.label}: rail visible`);
    assert.equal(
      await page.getByRole('button', { name: 'Open navigation' }).count(),
      0,
      `${size.label}: no hamburger`,
    );
    const beside = await page.evaluate(() => {
      const rail = document.querySelector('.rail').getBoundingClientRect();
      const main = document.querySelector('.workspace-main').getBoundingClientRect();
      const settings = [...document.querySelectorAll('.rail-foot button')].pop();
      return {
        railRight: rail.right,
        mainLeft: main.left,
        mainWidth: main.width,
        utilityBottom: settings.getBoundingClientRect().bottom,
        height: innerHeight,
      };
    });
    assert.ok(beside.railRight <= beside.mainLeft + 1, `${size.label}: ${JSON.stringify(beside)}`);
    assert.ok(beside.mainWidth >= 240, `${size.label}: ${JSON.stringify(beside)}`);
    assert.ok(
      beside.utilityBottom <= beside.height + 1,
      `${size.label}: the rail's utilities stay reachable ${JSON.stringify(beside)}`,
    );
    await noOverflow(size.label);
  }
  await shot('home-tablet-split');

  // ---- 200% zoom is a genuinely smaller layout viewport -------------------
  await page.setViewportSize({ width: 195, height: 422 });
  await page.goto(`${base}?at=/forms`);
  await page.locator('[data-testid="text-field"]').waitFor();
  await noOverflow('200% zoom');

  assert.deepEqual(errors, []);
  console.log(
    'PASS: 16px touch fields with larger text preserved, scalable viewport and gesture policy, scroll ownership and chaining, dialog/drawer scrolling with the page locked, keyboard avoidance for a dialog and a conversation without losing the reading position, pinch zoom kept apart from the keyboard, navigation at narrow/short/tablet widths, no horizontal overflow at 320px or 200% zoom',
  );
} finally {
  await browser?.close();
  await server.close();
}
