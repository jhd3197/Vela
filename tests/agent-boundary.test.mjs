/**
 * Phase 0 evidence for agent desktops: the managed browser stays inside its
 * boundary, and two desktops stay out of each other's way.
 *
 * The policy itself is a pure function, so most of it is checked directly. The
 * rest needs a real Chromium: route interception, WebSocket handshakes,
 * redirects, popups and service workers are claims about a browser, and a
 * mocked policy function would prove nothing about any of them.
 *
 * Two disposable HTTP servers stand in for the two things that matter: the
 * narrow gateway Vela publishes for app assets, and some other service on this
 * computer that the desktop must not be able to reach. Both bind to an ephemeral
 * loopback port, so the suite can run beside anything else.
 *
 * Without the browser runtime installed the browser half skips with a reason.
 * Install it with `python scripts/setup-browser-worker.py`.
 */

import assert from 'node:assert/strict';
import http from 'node:http';
import { after, before, describe, test } from 'node:test';

import {
  createPolicy,
  decide,
  isPrivateHost,
  normalizeOrigin,
  PolicyError,
  checkServerAddress,
} from '../scripts/browser-worker/src/network-policy.mjs';
import {
  checkCommandIdentity,
  createLineReader,
  encode,
  MAX_MESSAGE_BYTES,
  PROTOCOL_VERSION,
  ProtocolError,
} from '../scripts/browser-worker/src/protocol.mjs';

/* ------------------------------------------------------------- the policy -- */

describe('network policy', () => {
  const policy = createPolicy({
    gatewayOrigin: 'http://127.0.0.1:8765',
    gatewayPathPrefixes: ['/agent-host/', '/api/agent/'],
    sites: ['https://example.com', { origin: 'https://docs.test', includeSubdomains: true }],
  });

  test('the gateway is reachable only under its published paths', () => {
    assert.equal(decide('http://127.0.0.1:8765/agent-host/app/notes', policy).allowed, true);
    assert.equal(decide('http://127.0.0.1:8765/api/agent/effect', policy).allowed, true);
    const owner = decide('http://127.0.0.1:8765/api/settings', policy);
    assert.equal(owner.allowed, false);
    assert.equal(owner.reason, 'gateway_path_denied');
  });

  test('the rest of this computer is denied whatever spelling it arrives in', () => {
    for (const url of [
      'http://127.0.0.1:9999/',
      'http://localhost:9999/',
      'http://app.localhost/',
      'http://[::1]:9999/',
      'http://2130706433/', // 127.0.0.1 as one decimal
      'http://0x7f000001/', // and as hex
      'http://017700000001/', // and as octal
      'http://127.1/', // and short form
      'http://[::ffff:127.0.0.1]/', // and mapped into IPv6
      'http://192.168.1.10/',
      'http://10.0.0.5:3000/',
      'http://172.20.0.1/',
      'http://169.254.169.254/latest/meta-data/', // cloud metadata
      'http://printer.local/',
      'http://nas.home.arpa/',
    ]) {
      const verdict = decide(url, policy);
      assert.equal(verdict.allowed, false, `${url} should be denied`);
      assert.equal(verdict.reason, 'private_network_denied', url);
    }
  });

  test('non-network schemes never reach a page', () => {
    for (const url of [
      'file:///C:/Users/Juan/Documents/GitHub/vela/vela/api.py',
      'file:///etc/passwd',
      'chrome://version',
      'devtools://devtools/bundled/inspector.html',
      'view-source:https://example.com/',
      'filesystem:http://example.com/temporary/x',
      'chrome-extension://abcdef/background.html',
    ]) {
      const verdict = decide(url, policy);
      assert.equal(verdict.allowed, false, `${url} should be denied`);
      assert.equal(verdict.reason, 'scheme_denied', url);
    }
  });

  test('inert schemes a page makes for itself are allowed', () => {
    assert.equal(decide('about:blank', policy).allowed, true);
    assert.equal(decide('data:text/html,<p>hi', policy).allowed, true);
    assert.equal(decide('blob:http://example.com/1234', policy).allowed, true);
  });

  test('an approved site is an origin decision, not a text match', () => {
    assert.equal(decide('https://example.com/page', policy).allowed, true);
    assert.equal(decide('http://example.com/page', policy).allowed, false); // scheme differs
    assert.equal(decide('https://example.com.evil.test/', policy).allowed, false);
    assert.equal(decide('https://notexample.com/', policy).allowed, false);
    assert.equal(decide('https://sub.example.com/', policy).allowed, false); // not opted in
    assert.equal(decide('https://api.docs.test/v1', policy).allowed, true); // opted in
    assert.equal(decide('https://docs.test.evil.test/', policy).allowed, false);
  });

  test('ws and wss are judged by the same site rules as http and https', () => {
    assert.equal(decide('wss://example.com/socket', policy).allowed, true);
    assert.equal(decide('ws://example.com/socket', policy).allowed, false);
    assert.equal(decide('ws://127.0.0.1:9999/socket', policy).allowed, false);
  });

  test('a bad site rule is refused rather than guessed at', () => {
    assert.throws(() => normalizeOrigin('not a url'), PolicyError);
    assert.throws(() => normalizeOrigin('ftp://files.test'), PolicyError);
    assert.equal(normalizeOrigin('https://Example.com/some/path'), 'https://example.com');
    assert.equal(normalizeOrigin('https://example.com:8443/'), 'https://example.com:8443');
  });

  test('an approved name that resolves somewhere private is still refused', () => {
    assert.equal(
      checkServerAddress('https://example.com/', '192.168.1.50', policy).allowed,
      false,
      'DNS rebinding is not a URL problem, so the URL check cannot be the only one',
    );
    assert.equal(checkServerAddress('https://example.com/', '93.184.216.34', policy).allowed, true);
    assert.equal(checkServerAddress('http://127.0.0.1:8765/agent-host/x', '127.0.0.1', policy).allowed, true);
  });

  test('a host with no policy entry is denied, not defaulted open', () => {
    const empty = createPolicy({});
    assert.equal(decide('https://example.com/', empty).allowed, false);
    assert.equal(decide('https://example.com/', empty).reason, 'site_not_approved');
    assert.equal(isPrivateHost(''), true);
  });
});

/* ----------------------------------------------------------- the protocol -- */

describe('browser worker protocol', () => {
  const session = {
    desktopId: 'd1',
    runtimeSessionId: 'rs-1',
    controlEpoch: 3,
  };

  test('a command must name the desktop, runtime session and control generation it was issued for', () => {
    const good = { commandId: 'c1', name: 'open_view', desktopId: 'd1', runtimeSessionId: 'rs-1', controlEpoch: 3 };
    assert.equal(checkCommandIdentity(good, session), null);

    assert.equal(checkCommandIdentity({ ...good, desktopId: 'd2' }, session).code, 'identity_mismatch');
    assert.equal(
      checkCommandIdentity({ ...good, runtimeSessionId: 'rs-0' }, session).code,
      'identity_mismatch',
    );
    assert.equal(checkCommandIdentity({ ...good, controlEpoch: 2 }, session).code, 'stale_control_epoch');
    assert.equal(checkCommandIdentity({ ...good, commandId: '' }, session).code, 'protocol_error');
    assert.equal(checkCommandIdentity({ ...good, name: undefined }, session).code, 'protocol_error');
  });

  test('messages past the size bound are refused rather than truncated', () => {
    assert.throws(
      () => encode({ v: PROTOCOL_VERSION, type: 'result', text: 'x'.repeat(MAX_MESSAGE_BYTES) }),
      ProtocolError,
    );
  });

  test('the reader resynchronises after junk instead of growing without limit', () => {
    const seen = [];
    const errors = [];
    const read = createLineReader(
      (message) => seen.push(message),
      (detail, code) => errors.push({ detail, code }),
    );
    read('not json\n');
    read('{"v":99,"type":"ping"}\n');
    read('[1,2,3]\n');
    read(`${JSON.stringify({ v: PROTOCOL_VERSION, type: 'ping' })}\n`);
    assert.deepEqual(
      seen.map((message) => message.type),
      ['ping'],
    );
    assert.equal(errors.length, 3);
    assert.equal(errors[1].code, 'unsupported_version');
  });

  test('a split message is reassembled', () => {
    const seen = [];
    const read = createLineReader((message) => seen.push(message), () => {});
    const line = encode({ v: PROTOCOL_VERSION, type: 'ready', runtimeSessionId: 'rs-1' });
    read(line.slice(0, 10));
    read(line.slice(10));
    assert.equal(seen.length, 1);
    assert.equal(seen[0].runtimeSessionId, 'rs-1');
  });
});

/* --------------------------------------------- the boundary, in a browser -- */

/** Start a disposable HTTP server on an ephemeral loopback port. */
function serve(handler) {
  const server = http.createServer(handler);
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      resolve({ server, port, origin: `http://127.0.0.1:${port}` });
    });
  });
}

function close(fixture) {
  return new Promise((resolve) => fixture.server.close(resolve));
}

// Resolved before the suite is declared, because `describe`'s skip reason has to
// be a value by then. Imported through the worker's own module so
// `playwright-core` resolves from the worker's dependency tree.
let browserAvailable = false;
let skipReason = '';
let DesktopSession = null;
try {
  const module = await import('../scripts/browser-worker/src/session.mjs');
  const availability = module.browserAvailability();
  browserAvailable = availability.available;
  skipReason = availability.reason || '';
  DesktopSession = module.DesktopSession;
} catch (error) {
  skipReason = `the browser runtime is not installed (${error.message.split('\n')[0]})`;
}

let gateway;
let elsewhere;

before(async () => {
  if (!browserAvailable) return;

  // Some other service on this computer. Nothing about it is special; the point
  // is that it is not the gateway, so the desktop must not reach it.
  elsewhere = await serve((request, response) => {
    response.writeHead(200, { 'content-type': 'text/plain' });
    response.end('a private service answered');
  });
  elsewhere.server.on('upgrade', (request, socket) => {
    socket.destroy(); // Reaching here at all would already be the failure.
  });

  gateway = await serve((request, response) => {
    const url = new URL(request.url, gateway.origin);
    if (url.pathname === '/agent-host/redirect') {
      response.writeHead(302, { location: `${elsewhere.origin}/secret` });
      response.end();
      return;
    }
    if (url.pathname === '/agent-host/sw.js') {
      response.writeHead(200, { 'content-type': 'text/javascript' });
      response.end(
        `self.addEventListener('install', () => self.skipWaiting());
         self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
         self.addEventListener('fetch', (event) => event.respondWith(new Response('from a service worker')));`,
      );
      return;
    }
    if (url.pathname === '/agent-host/probe.txt') {
      response.writeHead(200, { 'content-type': 'text/plain' });
      response.end('from the network');
      return;
    }
    if (url.pathname === '/api/settings') {
      response.writeHead(200, { 'content-type': 'application/json' });
      response.end('{"owner":"this must never be reachable"}');
      return;
    }
    if (url.pathname.startsWith('/agent-host/')) {
      response.writeHead(200, {
        'content-type': 'text/html',
        'set-cookie': `desk=${url.searchParams.get('mark') || 'none'}; Path=/`,
      });
      response.end(`<!doctype html><title>Fixture app</title>
        <h1 id="heading">Fixture app</h1>
        <p id="cookie">${request.headers.cookie || 'no cookie'}</p>`);
      return;
    }
    response.writeHead(404);
    response.end('not found');
  });
});

after(async () => {
  if (gateway) await close(gateway);
  if (elsewhere) await close(elsewhere);
});

describe('managed browser boundary', { skip: browserAvailable ? false : skipReason || true }, () => {
  /** A session wired to the fixture gateway, with nothing else approved. */
  async function startSession(desktopId, runtimeSessionId) {
    const session = new DesktopSession({
      desktopId,
      runtimeSessionId,
      policy: createPolicy({
        gatewayOrigin: gateway.origin,
        gatewayPathPrefixes: ['/agent-host/'],
        sites: [],
      }),
    });
    await session.start({ headless: true });
    return session;
  }

  test('an app view opens through the gateway', async () => {
    const session = await startSession('d1', 'rs-1');
    try {
      const view = await session.openView('v1', `${gateway.origin}/agent-host/app/notes`);
      assert.equal(view.title, 'Fixture app');
      assert.equal(await session.page('v1').textContent('#heading'), 'Fixture app');
    } finally {
      await session.stop();
    }
  });

  test('the owner API is not reachable through the gateway the agent gets', async () => {
    const session = await startSession('d1', 'rs-1');
    try {
      await assert.rejects(
        () => session.openView('v1', `${gateway.origin}/api/settings`),
        /gateway_path_denied/,
      );
      await session.openView('v2', `${gateway.origin}/agent-host/app/notes`);
      const body = await session.page('v2').evaluate(async (origin) => {
        try {
          const response = await fetch(`${origin}/api/settings`);
          return await response.text();
        } catch (error) {
          return `blocked: ${error.message}`;
        }
      }, gateway.origin);
      assert.match(body, /^blocked:/, 'a page must not be able to fetch what it cannot navigate to');
    } finally {
      await session.stop();
    }
  });

  test('another local service is denied from navigation, fetch and WebSocket', async () => {
    const session = await startSession('d1', 'rs-1');
    try {
      await assert.rejects(
        () => session.openView('v1', `${elsewhere.origin}/secret`),
        /private_network_denied/,
      );

      await session.openView('v2', `${gateway.origin}/agent-host/app/notes`);
      const page = session.page('v2');

      const fetched = await page.evaluate(async (origin) => {
        try {
          const response = await fetch(`${origin}/secret`);
          return await response.text();
        } catch (error) {
          return `blocked: ${error.message}`;
        }
      }, elsewhere.origin);
      assert.match(fetched, /^blocked:/);

      const socket = await page.evaluate(
        (origin) =>
          new Promise((resolve) => {
            const url = origin.replace('http://', 'ws://');
            const ws = new WebSocket(`${url}/socket`);
            ws.onopen = () => resolve('open');
            ws.onerror = () => resolve('blocked');
            ws.onclose = () => resolve('blocked');
            setTimeout(() => resolve('timed out'), 4000);
          }),
        elsewhere.origin,
      );
      assert.equal(socket, 'blocked');

      const reasons = session.denialLog().map((entry) => entry.reason);
      assert.ok(reasons.includes('private_network_denied'));
      assert.ok(
        session.denialLog().some((entry) => entry.stage === 'websocket'),
        'the WebSocket handshake must be screened by the same policy, not left uncovered',
      );
    } finally {
      await session.stop();
    }
  });

  test('a redirect out of the gateway does not carry the view with it', async () => {
    const session = await startSession('d1', 'rs-1');
    try {
      await session.openView('v1', `${gateway.origin}/agent-host/app/notes`);
      const page = session.page('v1');
      await page.goto(`${gateway.origin}/agent-host/redirect`).catch(() => {});
      await page.waitForTimeout(250);
      assert.ok(
        !page.url().startsWith(elsewhere.origin),
        `the view ended up at ${page.url()}, which is outside the boundary`,
      );
      const content = await page.content();
      assert.ok(!content.includes('a private service answered'));
    } finally {
      await session.stop();
    }
  });

  test('a popup aimed outside the boundary is closed', async () => {
    const session = await startSession('d1', 'rs-1');
    try {
      await session.openView('v1', `${gateway.origin}/agent-host/app/notes`);
      const page = session.page('v1');
      await page.evaluate((origin) => window.open(`${origin}/secret`, '_blank'), elsewhere.origin);
      await page.waitForTimeout(500);
      assert.ok(
        session.denialLog().some((entry) => entry.stage === 'popup' || entry.stage === 'request'),
        'a popup is a new target, not a way out of the desktop',
      );
    } finally {
      await session.stop();
    }
  });

  test('a service worker never takes over, because route interception does not cover one', async () => {
    const session = await startSession('d1', 'rs-1');
    try {
      await session.openView('v1', `${gateway.origin}/agent-host/app/notes`);
      const page = session.page('v1');

      await page.evaluate(() => navigator.serviceWorker.register('/agent-host/sw.js').catch(() => {}));

      // What matters is not whether `register` threw — it is whether a worker
      // ever gets between the page and the screened network.
      const ready = await page.evaluate(() =>
        Promise.race([
          navigator.serviceWorker.ready.then(() => 'ready'),
          new Promise((resolve) => setTimeout(() => resolve('never ready'), 3000)),
        ]),
      );
      assert.equal(ready, 'never ready');
      assert.equal(await page.evaluate(() => Boolean(navigator.serviceWorker.controller)), false);

      await page.reload();
      const body = await page.evaluate(async () => (await fetch('/agent-host/probe.txt')).text());
      assert.equal(body, 'from the network', 'a worker must not be able to answer in place of the network');
    } finally {
      await session.stop();
    }
  });

  test('two desktops do not share cookies or pages', async () => {
    const one = await startSession('d1', 'rs-1');
    const two = await startSession('d2', 'rs-2');
    try {
      await one.openView('v1', `${gateway.origin}/agent-host/app/notes?mark=one`);
      await two.openView('v1', `${gateway.origin}/agent-host/app/notes?mark=two`);

      await one.page('v1').reload();
      await two.page('v1').reload();

      assert.match(await one.page('v1').textContent('#cookie'), /desk=one/);
      assert.match(await two.page('v1').textContent('#cookie'), /desk=two/);
      assert.doesNotMatch(await one.page('v1').textContent('#cookie'), /desk=two/);
    } finally {
      await one.stop();
      await two.stop();
    }
  });

  test('a view keeps working with no viewer attached, and its control generation invalidates stale commands', async () => {
    const session = await startSession('d1', 'rs-1');
    try {
      await session.openView('v1', `${gateway.origin}/agent-host/app/notes`);
      const page = session.page('v1');

      // Nothing here is watching. The deterministic step runs anyway: this is
      // the difference between a server-owned session and a hidden dashboard tab.
      await page.evaluate(() => {
        document.querySelector('#heading').textContent = 'step one done';
      });
      assert.equal(await page.textContent('#heading'), 'step one done');

      const command = {
        commandId: 'c1',
        name: 'click',
        desktopId: 'd1',
        runtimeSessionId: 'rs-1',
        controlEpoch: session.controlEpoch,
      };
      assert.equal(checkCommandIdentity(command, session), null);
      session.bumpControlEpoch(); // a human took over
      assert.equal(checkCommandIdentity(command, session).code, 'stale_control_epoch');
    } finally {
      await session.stop();
    }
  });

  test('a view supplies a real frame, tagged with the session that produced it', async () => {
    const session = await startSession('d1', 'rs-1');
    try {
      await session.openView('v1', `${gateway.origin}/agent-host/app/notes`);
      const frame = await session.captureFrame('v1');

      // A PNG, not a prepared sample image: the motion layer and the viewer both
      // depend on this being the view the agent is actually working in.
      assert.deepEqual(frame.image.subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
      assert.ok(frame.image.length > 1000);
      assert.equal(frame.width, 1280);
      assert.equal(frame.height, 800);
      assert.equal(frame.viewId, 'v1');
      assert.equal(frame.runtimeSessionId, 'rs-1');
      assert.equal(frame.controlEpoch, session.controlEpoch);
      assert.equal(typeof frame.deviceScaleFactor, 'number');

      await assert.rejects(() => session.captureFrame('v-missing'), /no view v-missing/);
    } finally {
      await session.stop();
    }
  });

  test('losing the browser is a bounded failure, not a hang, and leaves nothing running', async () => {
    const session = await startSession('d1', 'rs-1');
    let orphaned = true;
    try {
      await session.openView('v1', `${gateway.origin}/agent-host/app/notes`);
      const page = session.page('v1');

      // The browser goes away underneath a command in flight, the way a crash or
      // a killed process would. The outcome is settled here rather than with
      // `assert.rejects` so the rejection is never momentarily unhandled.
      const inFlight = page.waitForTimeout(30_000).then(
        () => 'finished',
        (error) => error,
      );
      await session.browser.close();

      const outcome = await inFlight;
      assert.ok(outcome instanceof Error, 'a command in flight must fail, not wait for a dead browser');
      assert.match(outcome.message, /closed|Target|crash/i);
      await assert.rejects(
        () => session.openView('v2', `${gateway.origin}/agent-host/app/notes`),
        /closed|Target|crash/i,
      );
      orphaned = session.browser.isConnected();
    } finally {
      await session.stop();
    }
    assert.equal(orphaned, false);
  });

  test('stopping a session leaves no browser behind', async () => {
    const session = await startSession('d1', 'rs-1');
    await session.openView('v1', `${gateway.origin}/agent-host/app/notes`);
    const browser = session.browser;
    await session.stop();
    assert.equal(browser.isConnected(), false);
    assert.equal(session.views.size, 0);
  });
});
