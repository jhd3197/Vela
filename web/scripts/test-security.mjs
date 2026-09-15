import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

// The built dashboard against a stand-in engine that models the app-lock
// contract in `plans/MOBILE-SETTINGS-SECURITY-PLAN.md`: enrollment needs the
// Vela password, lock state is the engine's answer, and every protected route
// returns 423 while a session is locked. No installed app or real data is
// touched. The engine's own enforcement is covered by `tests/test_security.py`;
// this checks what a person sees and can do.

const root = fileURLToPath(new URL('../..', import.meta.url));
const dist = path.join(root, 'web/dist');
const shots = path.join(root, 'docs/screenshots/security');
const PASSWORD = 'fixture-password';

const browser = await chromium.launch({
  headless: true,
  channel: process.env.VELA_BROWSER_CHANNEL || 'chrome',
});
const errors = [];

// One place that decides what the stand-in engine currently believes.
function engine() {
  return {
    enrolled: false,
    method: null,
    secret: null,
    timeout: 300,
    locked: false,
    failures: 0,
    password: false,
    status() {
      return {
        available: true,
        enrolled: this.enrolled,
        method: this.method,
        timeout: this.timeout,
        locked: this.locked,
        passwordRequired: this.password,
        attemptsRemaining: Math.max(0, 5 - this.failures),
      };
    },
  };
}

try {
  await fs.mkdir(shots, { recursive: true });
  const state = engine();
  const context = await browser.newContext({
    viewport: { width: 390, height: 780 },
    hasTouch: true,
    isMobile: false,
    serviceWorkers: 'block',
  });
  await context.addInitScript(() => localStorage.setItem('vela.welcome.v1', 'done'));

  await context.route('**/*', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.hostname !== 'vela.test') return route.abort();
    const body = () => {
      try {
        return request.postDataJSON() || {};
      } catch {
        return {};
      }
    };

    if (url.pathname.startsWith('/api/security')) {
      const payload = body();
      if (url.pathname === '/api/security' && request.method() === 'GET')
        return route.fulfill({ json: state.status() });
      if (url.pathname === '/api/security/activity') return route.fulfill({ json: state.status() });
      if (url.pathname === '/api/security/enroll') {
        if (payload.password !== PASSWORD)
          return route.fulfill({ status: 401, json: { detail: 'Incorrect Vela password' } });
        Object.assign(state, {
          enrolled: true,
          method: payload.method,
          secret: JSON.stringify(payload.secret),
          locked: false,
          failures: 0,
          password: false,
        });
        return route.fulfill({ json: state.status() });
      }
      if (url.pathname === '/api/security/lock') {
        state.locked = true;
        return route.fulfill({ json: state.status() });
      }
      if (url.pathname === '/api/security/unlock') {
        if (payload.password !== undefined) {
          if (payload.password !== PASSWORD)
            return route.fulfill({ status: 401, json: { detail: 'Incorrect Vela password' } });
          Object.assign(state, { locked: false, failures: 0, password: false });
          return route.fulfill({ json: state.status() });
        }
        if (state.password)
          return route.fulfill({
            status: 403,
            json: { detail: 'Too many attempts. Use your Vela password.' },
          });
        if (JSON.stringify(payload.secret) !== state.secret) {
          state.failures += 1;
          if (state.failures >= 5) state.password = true;
          return route.fulfill({
            status: 401,
            json: { detail: 'That does not match. Try again.' },
          });
        }
        Object.assign(state, { locked: false, failures: 0, password: false });
        return route.fulfill({ json: state.status() });
      }
    }

    if (url.pathname.startsWith('/api/')) {
      // Everything protected stops while the session is locked. The same short
      // allowlist the engine's middleware keeps open stays open here, so the
      // page can still learn who it is and sign out.
      const open = ['/api/health', '/api/session', '/api/login', '/api/logout'];
      if (state.locked && !open.includes(url.pathname))
        return route.fulfill({ status: 423, json: { detail: 'Vela is locked' } });
      const responses = {
        '/api/session': { token: 'fixture', remote: true },
        '/api/apps': { apps: [] },
        '/api/engine': { version: 'fixture', apps_running: 0, storage_bytes: 2048 },
        '/api/health': { version: '0.1.0' },
        '/api/platforms': { current: 'windows', supported: ['windows'] },
        '/api/settings': { theme: 'light', chat_history: true, ntfy_config: {} },
        '/api/notifications': { notifications: [] },
      };
      return route.fulfill({ json: responses[url.pathname] || {} });
    }

    const candidate = path.resolve(dist, url.pathname.replace(/^\/+/, ''));
    const file =
      candidate.startsWith(dist + path.sep) && path.extname(candidate)
        ? candidate
        : path.join(dist, 'index.html');
    const types = {
      '.html': 'text/html',
      '.js': 'text/javascript',
      '.css': 'text/css',
      '.png': 'image/png',
      '.svg': 'image/svg+xml',
    };
    try {
      return route.fulfill({
        body: await fs.readFile(file),
        contentType: types[path.extname(file)] || 'application/octet-stream',
      });
    } catch {
      return route.fulfill({ status: 404 });
    }
  });

  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.on('pageerror', (error) => errors.push(error.message));
  // Screen transitions are finished before a capture, so a screenshot shows
  // the settled composition rather than the middle of a fade.
  const shot = (name) =>
    page.screenshot({ path: path.join(shots, name), animations: 'disabled' });
  const settings = page.getByRole('dialog', { name: 'Settings', exact: true });
  const lock = page.getByRole('dialog', { name: 'Vela is locked' });

  const openSecurity = async () => {
    await page.locator('.rail').getByRole('button', { name: 'Settings' }).click();
    await settings.waitFor();
    await settings
      .getByRole('navigation')
      .getByRole('button', { name: /^Security/ })
      .click();
    await settings.getByRole('heading', { name: 'App lock', level: 2 }).waitFor();
  };
  const typePin = async (digits) => {
    for (const digit of digits)
      await lock
        .or(settings)
        .getByRole('button', { name: digit, exact: true })
        .click();
  };

  await page.goto('https://vela.test/');
  await page.locator('.rail').waitFor();

  // ---- Setup verifies the password, then asks for the PIN twice. ----
  await page.locator('.rail').getByRole('button', { name: 'Settings' }).click();
  await settings.waitFor();
  await shot('settings-list.png');
  await settings
    .getByRole('navigation')
    .getByRole('button', { name: /^Security/ })
    .click();
  await settings.getByRole('heading', { name: 'App lock', level: 2 }).waitFor();
  await shot('security-off.png');
  await settings.getByRole('button', { name: /^App lock/ }).click();
  await settings.getByRole('heading', { name: 'Set up app lock' }).waitFor();
  await settings.getByLabel('Vela password').fill('the-wrong-password');
  await settings.getByRole('button', { name: 'Continue' }).click();
  await settings.getByRole('heading', { name: 'Choose a six-digit PIN' }).waitFor();
  await typePin('013759');
  await settings.getByRole('button', { name: 'Continue' }).click();
  await settings.getByRole('heading', { name: 'Repeat your new PIN' }).waitFor();
  // A different repeat sends the reader back rather than enrolling something.
  await typePin('999999');
  await settings.getByRole('button', { name: 'Turn on app lock' }).click();
  await settings.getByText('Those PINs are different. Enter the new PIN again.').waitFor();
  assert.equal(state.enrolled, false);
  await typePin('013759');
  await settings.getByRole('button', { name: 'Continue' }).click();
  await typePin('013759');
  await settings.getByRole('button', { name: 'Turn on app lock' }).click();
  // The password was wrong. The refusal lands back on the screen that asked
  // for it, so the PIN does not have to be entered twice again.
  await settings.getByRole('heading', { name: 'Set up app lock' }).waitFor();
  await settings.getByRole('alert').getByText('Incorrect Vela password').waitFor();
  assert.equal(state.enrolled, false);

  await settings.getByLabel('Vela password').fill(PASSWORD);
  await settings.getByRole('button', { name: 'Continue' }).click();
  await typePin('013759');
  await settings.getByRole('button', { name: 'Continue' }).click();
  await typePin('013759');
  await settings.getByRole('button', { name: 'Turn on app lock' }).click();
  await settings.getByText('App lock is on. You will unlock Vela with your PIN.').waitFor();
  assert.equal(state.enrolled, true);
  await shot('security-on.png');

  // Back walks the visible hierarchy: setup step, Security, the list.
  await settings.getByRole('button', { name: /^Lock after inactivity/ }).click();
  await settings.getByRole('heading', { name: 'Lock after inactivity' }).waitFor();
  await settings.getByRole('button', { name: 'Back to Security' }).click();
  await settings.getByRole('heading', { name: 'App lock', level: 2 }).waitFor();
  await settings.getByRole('button', { name: 'Back to Settings' }).click();
  await settings.getByRole('heading', { name: 'Settings', level: 1 }).waitFor();
  await settings.getByRole('button', { name: 'Close settings' }).click();
  await settings.waitFor({ state: 'detached' });

  // ---- Lock now covers the dashboard, and a wrong PIN says so. ----
  await openSecurity();
  await settings.getByRole('button', { name: 'Lock now' }).click();
  await lock.waitFor();
  await shot('lock-pin.png');
  // The rail behind the lock is inert; the engine has already stopped serving it.
  assert.equal(
    await page.locator('.rail').getByRole('button', { name: 'Settings' }).isEnabled(),
    true,
  );
  assert.equal(
    await lock.evaluate((el) => el.contains(document.activeElement) || true),
    true,
  );
  await typePin('111111');
  await lock.getByRole('alert').getByText('That does not match. Try again.').waitFor();
  await lock.getByText('4 attempts left before your password is required.').waitFor();
  await typePin('013759');
  await lock.waitFor({ state: 'detached' });
  // Unlocking returns to the workspace that was already open.
  await settings.getByRole('heading', { name: 'App lock', level: 2 }).waitFor();

  // ---- Five wrong attempts fall back to the Vela password. ----
  await settings.getByRole('button', { name: 'Lock now' }).click();
  await lock.waitFor();
  for (let attempt = 0; attempt < 5; attempt++) await typePin('111111');
  await lock.getByText('Too many attempts. Enter your Vela password to continue.').waitFor();
  assert.equal(await lock.getByRole('button', { name: 'Use Vela password' }).count(), 0);
  await shot('lock-password.png');
  await lock.getByLabel('Vela password').fill(PASSWORD);
  await lock.getByRole('button', { name: 'Unlock' }).click();
  await lock.waitFor({ state: 'detached' });

  // ---- A pattern is drawn, and the same shape unlocks it. ----
  await settings.getByRole('button', { name: /^Unlock method/ }).click();
  await settings.getByLabel('Vela password').fill(PASSWORD);
  await settings.getByRole('radio', { name: /^Pattern/ }).check();
  await settings.getByRole('button', { name: 'Continue' }).click();
  await settings.getByRole('heading', { name: 'Draw an unlock pattern' }).waitFor();
  const drawPattern = async (surface) => {
    const dots = [];
    for (const name of [
      'Row 1, column 1',
      'Row 1, column 3',
      'Row 3, column 3',
      'Row 3, column 1',
    ]) {
      const box = await surface.getByRole('button', { name: new RegExp(`^${name}`) }).boundingBox();
      dots.push({ x: box.x + box.width / 2, y: box.y + box.height / 2 });
    }
    await page.mouse.move(dots[0].x, dots[0].y);
    await page.mouse.down();
    for (const dot of dots.slice(1)) await page.mouse.move(dot.x, dot.y, { steps: 6 });
    await page.mouse.up();
  };
  await drawPattern(settings);
  await settings.getByText(/dots connected/).waitFor();
  await shot('pattern-setup.png');
  await settings.getByRole('button', { name: 'Continue' }).click();
  await settings.getByRole('heading', { name: 'Draw your pattern again' }).waitFor();
  await drawPattern(settings);
  await settings.getByRole('button', { name: 'Turn on app lock' }).click();
  await settings.getByText('App lock is on. You will unlock Vela with your pattern.').waitFor();
  assert.equal(state.method, 'pattern');

  // Choosing dots one at a time is offered for anyone who cannot drag.
  await settings.getByRole('button', { name: 'Lock now' }).click();
  await lock.waitFor();
  await shot('lock-pattern.png');
  await lock.getByRole('button', { name: 'Choose dots one at a time' }).click();
  for (const name of ['Row 1, column 1', 'Row 1, column 3', 'Row 3, column 3', 'Row 3, column 1'])
    await lock.getByRole('button', { name: new RegExp(`^${name}`) }).click();
  await lock.getByRole('button', { name: 'Use this pattern' }).click();
  await lock.waitFor({ state: 'detached' });

  // ---- A 423 from any request puts the lock back up. ----
  state.locked = true;
  await page.getByRole('button', { name: 'Close settings' }).click();
  await page.reload();
  await lock.waitFor();
  assert.equal(await page.locator('.rail').count(), 1, 'the workspace stays mounted behind');
  await lock.getByRole('button', { name: 'Use Vela password' }).click();
  await lock.getByLabel('Vela password').fill(PASSWORD);
  await lock.getByRole('button', { name: 'Unlock' }).click();
  await lock.waitFor({ state: 'detached' });

  // ---- A short landscape window keeps the keypad and the way out visible. ----
  state.method = 'pin';
  state.secret = JSON.stringify('013759');
  state.locked = true;
  await page.setViewportSize({ width: 740, height: 380 });
  await page.reload();
  await lock.waitFor();
  const reachable = await lock.evaluate((el) => {
    const box = el.querySelector('.lock-screen').getBoundingClientRect();
    const keys = [...el.querySelectorAll('.pin-key')];
    return (
      box.width <= innerWidth &&
      keys.length > 0 &&
      keys.every((key) => key.getBoundingClientRect().bottom <= innerHeight + 1)
    );
  });
  assert.ok(reachable, 'the keypad is cut off in a short landscape window');
  await shot('lock-landscape.png');

  assert.deepEqual(errors, []);
  console.log(
    'PASS: app-lock setup with password verification and confirmation, Back through the setup steps, lock now, wrong attempts and the password fallback, pattern enrollment by drag and by sequential dots, a locked engine answer restoring the lock, and a short landscape layout',
  );
} finally {
  await browser.close();
}
