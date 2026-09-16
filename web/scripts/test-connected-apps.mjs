// Real HTTPS upstream + disposable Vela. No personal services or data are used.
import { createRequire } from 'node:module';
import { spawn, execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs/promises';
import os from 'node:os';
import https from 'node:https';
import assert from 'node:assert/strict';
const { chromium } = createRequire(import.meta.url)('playwright');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'vela-web-apps-'));
const cert = path.join(temp, 'cert.pem'),
  key = path.join(temp, 'key.pem');
const port = 17716;
const base = `https://127.0.0.1:${port}`;
let browser, server, upstream;
let output = '';
const requests = [];
try {
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
      '/CN=localhost',
      '-addext',
      'subjectAltName=DNS:localhost,IP:127.0.0.1',
    ],
    { windowsHide: true, stdio: 'ignore' },
  );
  upstream = https.createServer(
    { key: await fs.readFile(key), cert: await fs.readFile(cert) },
    (req, res) => {
      requests.push({ url: req.url, headers: req.headers });
      if (req.url === '/redirect') {
        res.writeHead(302, { Location: `https://127.0.0.1:${upstream.address().port}/escaped` });
        return res.end();
      }
      if (req.url === '/login') {
        res.writeHead(303, {
          'Set-Cookie': 'reader=logged-in; Path=/; Secure; HttpOnly; SameSite=None',
          Location: '/',
        });
        return res.end();
      }
      if (req.url === '/session') {
        res.setHeader('Content-Type', 'application/json');
        return res.end(
          JSON.stringify({ signedIn: (req.headers.cookie || '').includes('reader=logged-in') }),
        );
      }
      if (req.url === '/blocked') res.setHeader('X-Frame-Options', 'DENY');
      res.setHeader('Content-Type', 'text/html');
      res.end(`<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
      <title>Reading room</title><style>body{font:16px system-ui;background:#fbf8ef;color:#26352c;padding:24px;max-width:660px;margin:auto}h1{font:36px Georgia}input,button{font:inherit;padding:10px;max-width:90%}label{display:block;margin:24px 0 8px}button{margin:8px 0}p{line-height:1.6}</style></head><body>
      <p>DISPOSABLE WEB SERVICE</p><h1>Reading room</h1><p>Your reading list, served by its own website.</p>
      <form action="/login" method="post"><button>Sign in to Reading</button></form>
      <p id="session">Checking sign-in…</p>
      <label for="book">Book title</label><input id="book"><button id="save">Save book</button><p id="saved"></p>
      <script>book.value=localStorage.getItem('book')||'';
      document.querySelector('#save').onclick=()=>{localStorage.setItem('book',book.value);document.querySelector('#saved').textContent='Saved: '+book.value};
      fetch('/session').then(r=>r.json()).then(s=>document.querySelector('#session').textContent=s.signedIn?'Signed in to Reading':'Not signed in');
      </script></body></html>`);
    },
  );
  await new Promise((resolve) => upstream.listen(0, '127.0.0.1', resolve));
  const service = `https://localhost:${upstream.address().port}`;
  const python =
    process.env.VELA_TEST_PYTHON ||
    path.join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
  server = spawn(
    python,
    ['scripts/serve-connection-fixtures.py', '--cert', cert, '--key', key, '--port', String(port)],
    { cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] },
  );
  server.stdout.on('data', (chunk) => {
    output += chunk;
  });
  server.stderr.on('data', (chunk) => {
    output += chunk;
  });
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw new Error(output);
    try {
      await new Promise((resolve, reject) =>
        https
          .get(base + '/api/health', { rejectUnauthorized: false }, (response) => {
            response.resume();
            response.on('end', resolve);
          })
          .on('error', reject),
      );
      break;
    } catch {
      if (i === 99) throw new Error(output);
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  browser = await chromium.launch({
    headless: true,
    channel: process.env.VELA_BROWSER_CHANNEL || 'chrome',
  });
  const shots = path.join(root, 'docs/screenshots/connected-apps');
  await fs.mkdir(shots, { recursive: true });
  for (const viewport of [
    { width: 1366, height: 900 },
    { width: 390, height: 844 },
  ]) {
    const context = await browser.newContext({
      viewport,
      ignoreHTTPSErrors: true,
      isMobile: viewport.width < 500,
      hasTouch: viewport.width < 500,
    });
    const page = await context.newPage();
    page.setDefaultTimeout(12000);
    const sessionCalls = [];
    page.on('request', (request) => {
      if (/\/api\/apps\/web--.*\/session/.test(request.url())) sessionCalls.push(request.url());
    });
    await page.goto(base + '/library');
    await page.getByLabel('Password', { exact: true }).fill('fixture-password-123');
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.getByRole('button', { name: 'Add an app', exact: true }).click();
    await page.getByRole('radio', { name: /Connect a website/ }).click();
    await page.getByRole('button', { name: 'Set up a connection', exact: true }).click();
    await page.getByLabel('App name', { exact: true }).fill(`Reading ${viewport.width}`);
    await page.getByLabel('Web address').fill(base + '/library');
    await page
      .getByRole('dialog')
      .getByRole('button', { name: 'Add web app', exact: true })
      .click();
    await page.getByRole('alert').filter({ hasText: 'different hostname' }).waitFor();
    await page.getByLabel('Web address').fill(service);
    await page.screenshot({ path: path.join(shots, `setup-${viewport.width}.png`) });
    await page
      .getByRole('dialog')
      .getByRole('button', { name: 'Add web app', exact: true })
      .click();
    // A connected app is installed, so it lives in the Marketplace's Installed
    // tab; open its row to reach the detail drawer.
    await page.goto(base + '/library?tab=installed');
    await page
      .locator('.group-row')
      .filter({ hasText: `Reading ${viewport.width}` })
      .click();
    await page.getByRole('dialog').getByRole('button', { name: 'Open', exact: true }).click();
    const appUrl = page.url();
    const frame = page.frameLocator('iframe').frameLocator('iframe');
    await frame.getByRole('heading', { name: 'Reading room' }).waitFor();
    await frame.getByRole('button', { name: 'Sign in to Reading' }).click();
    await frame.getByText('Signed in to Reading', { exact: true }).waitFor();
    await frame.getByLabel('Book title').fill('A book saved in the service');
    await frame.getByRole('button', { name: 'Save book' }).click();
    await frame.getByText('Saved: A book saved in the service').waitFor();
    const child = page.frames().find((item) => item.url().startsWith(service));
    const boundary = await child.evaluate(() => {
      let parentDenied = false,
        topDenied = false;
      try {
        parent.document.body;
      } catch {
        parentDenied = true;
      }
      try {
        top.document.body;
      } catch {
        topDenied = true;
      }
      return { parentDenied, topDenied, sdk: typeof window.Vela, referrer: document.referrer };
    });
    assert.deepEqual(boundary, {
      parentDenied: true,
      topDenied: true,
      sdk: 'undefined',
      referrer: service + '/',
    });
    assert.deepEqual(sessionCalls, []);
    await page.getByRole('button', { name: 'Reload web app' }).click();
    await frame.getByRole('heading', { name: 'Reading room' }).waitFor();
    assert.equal(await frame.getByLabel('Book title').inputValue(), 'A book saved in the service');
    const box = await page.locator('.appview-frame').boundingBox();
    assert.ok(box.height > 400 && box.y + box.height <= viewport.height + 1);
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      true,
    );
    await page.screenshot({ path: path.join(shots, `workspace-${viewport.width}.png`) });
    const popupPromise = context.waitForEvent('page');
    await page.getByRole('link', { name: 'Open in browser' }).click();
    const popup = await popupPromise;
    await popup.waitForLoadState();
    assert.equal(await popup.evaluate(() => window.opener === null), true);
    await popup.close();

    // Same-origin form navigation works; navigation to any other origin is blocked.
    await page.getByRole('button', { name: 'Edit connection' }).click();
    await page.getByLabel('Web address').fill(service + '/redirect');
    await page.getByRole('button', { name: 'Save changes' }).click();
    await page.waitForFunction(() =>
      document.querySelector('iframe')?.srcdoc.includes('/redirect'),
    );
    await page.waitForTimeout(800);
    assert.equal(
      requests.some((request) => request.url === '/escaped'),
      false,
    );
    assert.equal(page.url(), appUrl);
    await page.getByRole('button', { name: 'Edit connection' }).click();
    await page.getByLabel('Web address').fill(service + '/blocked');
    await page.getByRole('button', { name: 'Save changes' }).click();
    await page.waitForFunction(() => document.querySelector('iframe')?.srcdoc.includes('/blocked'));
    await page.getByRole('link', { name: 'Open in browser' }).waitFor();
    // The host rail, on screen at every width, is the way out; the frame has
    // no chrome.
    await page.locator('.rail').getByRole('link', { name: 'Marketplace', exact: true }).click();
    await page.waitForURL(base + '/library');
    await page.goto(base + '/library?tab=installed');
    await page
      .locator('.group-row')
      .filter({ hasText: `Reading ${viewport.width}` })
      .click();
    await page.getByRole('button', { name: 'Edit connection' }).click();
    await page.getByRole('button', { name: 'Remove connection' }).click();
    await page.waitForFunction(() => document.querySelectorAll('dialog[open]').length === 0);
    assert.equal(
      await page
        .locator('.group-row')
        .filter({ hasText: `Reading ${viewport.width}` })
        .count(),
      0,
    );
    await page.goto(appUrl);
    await page.getByRole('heading', { name: 'App not found' }).waitFor();
    await context.close();
  }
  assert.ok(requests.length > 0);
  for (const request of requests) {
    assert.equal(request.headers.authorization, undefined);
    assert.ok(!(request.headers.cookie || '').includes('__Host-vela-session'));
    assert.ok(!request.headers.referer || request.headers.referer.startsWith('https://localhost:'));
  }
  console.log(
    'Connected web apps: desktop/mobile CRUD, sign-in, storage, origin isolation, redirect restrictions, fallback and removal passed.',
  );
} finally {
  await browser?.close();
  if (server && server.exitCode === null) {
    server.kill();
    await new Promise((resolve) => server.once('exit', resolve));
  }
  if (upstream) await new Promise((resolve) => upstream.close(resolve));
  await fs.rm(temp, { recursive: true, force: true });
}
