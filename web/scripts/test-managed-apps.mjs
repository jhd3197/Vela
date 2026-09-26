// A managed web app, in a real browser, on its own hostname.
//
// This suite exists for the four things an HTTP client cannot establish, and
// each of them was a real question rather than a formality:
//
// 1. Chrome resolves `<app>.apps.localhost` to this computer with no hosts file
//    and no DNS server. The whole address design rests on that.
// 2. Chrome treats that origin as trustworthy over plain HTTP, and therefore
//    accepts the gateway's `Secure` `__Host-` session cookie. `httpx` applies
//    the plain RFC rule and drops it, so the Python suite cannot ask this.
// 3. The `__Host-` prefix really does stop a page on a sibling app's hostname
//    from tossing a session cookie onto the shared parent name -- which
//    `SameSite` cannot, because those names are same-site.
// 4. A launch ticket can only be spent by going to it. A `fetch` from a page on
//    a sibling hostname reaching the exchange is a request that arrived even
//    when CORS blocks the reply, and it used to spend the ticket.
//
// Real disposable service, disposable Vela, no personal data.
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs/promises';
import http from 'node:http';
import assert from 'node:assert/strict';
const { chromium } = createRequire(import.meta.url)('playwright');

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const port = 17717;
const base = `http://127.0.0.1:${port}`;
const appId = 'fixture-notes';
const appOrigin = `http://${appId}.apps.localhost:${port}`;
let browser, server;
let output = '';

// The rail shows a running app too, and clicking that navigates instead of
// opening its details. The library row is the one carrying the version line.
const libraryRow = (page) =>
  page.getByRole('button', { name: /Fixture Notes[\s\S]*v1\.0\.0/ }).first();

const get = (url, options = {}) =>
  new Promise((resolve, reject) => {
    http
      .get(url, options, (response) => {
        let body = '';
        response.on('data', (chunk) => (body += chunk));
        response.on('end', () => resolve({ status: response.statusCode, body }));
      })
      .on('error', reject);
  });

try {
  const python =
    process.env.VELA_TEST_PYTHON ||
    path.join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
  server = spawn(python, ['scripts/serve-managed-fixtures.py', '--port', String(port)], {
    cwd: root,
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  server.stdout.on('data', (chunk) => (output += chunk));
  server.stderr.on('data', (chunk) => (output += chunk));
  for (let i = 0; ; i++) {
    if (server.exitCode !== null) throw new Error(output);
    try {
      await get(base + '/api/health');
      break;
    } catch {
      if (i === 300) throw new Error('fixture server did not start\n' + output);
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }

  browser = await chromium.launch({
    headless: true,
    channel: process.env.VELA_BROWSER_CHANNEL || 'chrome',
  });
  const shots = path.join(root, 'docs/screenshots/managed-apps');
  await fs.mkdir(shots, { recursive: true });

  for (const viewport of [
    { width: 1366, height: 900 },
    { width: 390, height: 844 },
  ]) {
    const wide = viewport.width > 500;
    const context = await browser.newContext({
      viewport,
      isMobile: !wide,
      hasTouch: !wide,
    });
    const page = await context.newPage();
    page.setDefaultTimeout(30000);

    // 1. The app is in the Library, installed and not running. Nothing started
    //    because a page was looked at.
    await page.goto(base + '/library?tab=installed');
    await libraryRow(page).click();
    await page.getByRole('heading', { name: 'Fixture Notes' }).waitFor();
    await page.getByRole('button', { name: /^Start$/ }).click();
    await page.getByText('Running', { exact: true }).first().waitFor();
    await page.screenshot({ path: path.join(shots, `settings-${viewport.width}.png`) });

    // 2. Open it. The window frames the app's own hostname, and Vela cannot see
    //    inside: the frame is cross-origin by construction.
    await page.keyboard.press('Escape');
    await page.goto(base + `/app/${appId}`);
    const frame = page.frameLocator('iframe.appview-frame');
    await frame.getByRole('heading', { name: 'Fixture app' }).waitFor();
    const src = await page.locator('iframe.appview-frame').getAttribute('src');
    assert.equal(new URL(src).host, `${appId}.apps.localhost:${port}`, src);
    assert.match(new URL(src).pathname, /^\/_vela\/enter$/, src);
    await page.screenshot({ path: path.join(shots, `window-${viewport.width}.png`) });

    // Chrome resolved the name itself, and treats the origin as trustworthy --
    // which is the only reason a `Secure` `__Host-` cookie can be set on it.
    const inside = page.frames().find((candidate) => candidate.url().startsWith(appOrigin));
    assert.ok(inside, 'the app frame did not load on its own origin');
    assert.equal(await inside.evaluate(() => window.isSecureContext), true);

    // 3. The session cookie exists, is host-only, and no page script can read it.
    const cookies = await context.cookies(appOrigin);
    const session = cookies.find((cookie) => cookie.name === '__Host-vela-app');
    assert.ok(session, 'the browser did not keep the gateway session cookie');
    assert.equal(session.domain, `${appId}.apps.localhost`);
    assert.equal(session.httpOnly, true);
    assert.equal(session.secure, true);
    assert.equal(session.path, '/');
    assert.equal(
      await inside.evaluate(() => document.cookie.includes('__Host-vela-app')),
      false,
      'the session cookie was readable by page script',
    );

    // 4. The app's own credentials work through the gateway, and Vela's API is
    //    not on this origin at all.
    const signedIn = await inside.evaluate(async () => {
      const login = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user: 'tester', password: 'fixture-pw' }),
      }).then((r) => r.json());
      const created = await fetch('/api/notes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + login.token },
        body: JSON.stringify({ body: 'written from a browser' }),
      });
      return {
        user: login.user,
        created: created.status,
        // Its own cookie alone, without the bearer, is enough afterwards --
        // provided the application said `SameSite=None`, which is what an
        // application has to say to be usable inside a window at all.
        me: await fetch('/api/me').then((r) => r.json()),
        velaApi: await fetch('/api/settings').then((r) => r.status),
        // What the browser actually sent the application. The `Lax` cookie the
        // fixture also set is the one a window cannot carry.
        sent: await fetch('/api/received').then((r) => r.json()),
      };
    });
    assert.equal(signedIn.user, 'tester');
    assert.equal(signedIn.created, 201);
    assert.equal(signedIn.me.user, 'tester', 'the app lost its own sign-in inside the window');
    assert.equal(signedIn.velaApi, 404, 'Vela answered its own API on an app hostname');
    assert.ok(
      signedIn.sent.cookies.includes('fixture_session'),
      `the app's SameSite=None cookie did not reach it: ${signedIn.sent.cookies}`,
    );
    assert.ok(
      !signedIn.sent.cookies.includes('fixture_lax'),
      'a SameSite=Lax cookie reached a cross-site frame, which no browser should do',
    );
    assert.ok(
      !signedIn.sent.cookies.some((name) => name.startsWith('__Host-vela')),
      `Vela's own cookie reached the application: ${signedIn.sent.cookies}`,
    );

    // 5. An app cookie loses its Domain, so a sibling hostname sees nothing --
    //    and a sibling cannot toss a `__Host-` cookie at the shared parent name.
    await inside.evaluate(() => fetch('/api/set-cookies'));
    const sibling = await context.newPage();
    await sibling.goto(`http://other-app.apps.localhost:${port}/`);
    await sibling.getByRole('heading', { name: 'This app is not installed' }).waitFor();
    const siblingCookies = await sibling.evaluate(() => {
      document.cookie = '__Host-vela-app=stolen; Path=/; Domain=apps.localhost; Secure';
      return document.cookie;
    });
    assert.equal(siblingCookies, '', `a sibling hostname could read cookies: ${siblingCookies}`);
    await sibling.close();

    // 6. A launch ticket is only spendable by going to it.
    const ticket = await page.evaluate(async (id) => {
      const token = await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } })
        .then((r) => r.json())
        .then((s) => s.token);
      return fetch(`/api/managed/${id}/launch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
        body: '{}',
      }).then((r) => r.json());
    }, appId);
    const link = new URL(ticket.url);
    const spent = await inside.evaluate(async (search) => {
      const asFetch = await fetch('/_vela/enter' + search).then((r) => r.status);
      const asImage = await new Promise((done) => {
        const image = new Image();
        image.onload = () => done('loaded');
        image.onerror = () => done('refused');
        image.src = '/_vela/enter' + search;
      });
      return { asFetch, asImage };
    }, link.search);
    assert.equal(spent.asFetch, 400, 'a fetch reached the launch exchange');
    assert.equal(spent.asImage, 'refused');
    // And the ticket those refusals did not spend still opens the app.
    const visitor = await context.newPage();
    await visitor.goto(ticket.url);
    await visitor.getByRole('heading', { name: 'Fixture app' }).waitFor();
    assert.equal(new URL(visitor.url()).pathname, '/');
    await visitor.close();

    // 7. Closing the window does not stop the service; Stop does.
    await page.goto(base + '/library?tab=installed');
    const still = await page.evaluate(async (id) => {
      const token = await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } })
        .then((r) => r.json())
        .then((s) => s.token);
      return fetch(`/api/managed/${id}/status`, { headers: { Authorization: 'Bearer ' + token } })
        .then((r) => r.json())
        .then((s) => s.managed.state);
    }, appId);
    assert.equal(still, 'ready', 'closing the window stopped the service');

    await libraryRow(page).click();
    await page.getByRole('heading', { name: 'Fixture Notes' }).waitFor();
    await page.getByText('Running', { exact: true }).first().waitFor();
    await page.getByRole('button', { name: /^Stop$/ }).click();
    await page.getByText('Stopped', { exact: true }).first().waitFor();

    // 8. Stopping revokes what was open: the same browser is shown the way in
    //    again rather than a stale page.
    const closed = await context.newPage();
    await closed.goto(appOrigin + '/');
    await closed.getByRole('heading', { name: /Open Fixture Notes from Vela/ }).waitFor();
    await closed.close();

    await context.close();
  }

  console.log(
    'PASS: a managed web app resolves on its own hostname in Chrome, keeps a host-only ' +
      'HttpOnly session cookie, carries its own sign-in, hides Vela from that origin, ' +
      'isolates its cookies from a sibling name, refuses a launch link that is fetched ' +
      'rather than visited, survives its window closing, and is reachable no longer once stopped',
  );
} catch (error) {
  console.error(output);
  throw error;
} finally {
  await browser?.close();
  server?.kill();
}
