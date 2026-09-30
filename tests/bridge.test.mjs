import test from 'node:test';
import assert from 'node:assert/strict';
import { createBridge } from '../web/src/bridge/host.js';

test('bridge binds source, opaque origin, protocol, nonce and operation; tokens stay in host', async () => {
  let listener;
  globalThis.addEventListener = (_type, callback) => { listener = callback; };
  globalThis.removeEventListener = () => {};
  const messages = [], requests = [], announced = [];
  const source = { postMessage: (message) => messages.push(message) };
  let ready = false;
  const bridge = createBridge({ frame: { contentWindow: source },
    session: { token: 'scoped-secret', installationId: 'installation-one', capabilities: ['storage'] },
    context: { installationId: 'installation-one' },
    onReady: () => { ready = true; }, onDirty() {}, onNavigate() {}, onError() {},
    // The handshake reports what the app's SDK can do. Counted separately
    // throughout, so the assertions below stay about what the app asked for.
    fetcher: async (path, options) => { if (path !== '/api/app/features') requests.push({ path, options }); else announced.push(options); return new Response(JSON.stringify({ revision: 1, value: 'own data' })); },
  });
  const hello = { type: 'vela:ready', protocol: 1 };
  await listener({ source: {}, origin: 'null', data: hello });
  await listener({ source, origin: 'https://evil.example', data: hello });
  await listener({ source, origin: 'null', data: { ...hello, protocol: 9 } });
  assert.equal(ready, false);
  await listener({ source, origin: 'null', data: hello });
  assert.equal(ready, true);
  assert.equal(announced.length, 1, 'the engine is told what this app can do');
  assert.deepEqual(JSON.parse(announced[0].body), { features: [] }, 'an SDK that announces nothing announces nothing');
  const nonce = messages[0].session;
  const request = { type: 'vela:request', protocol: 1, id: 'one', session: nonce, operation: 'storage.read', payload: {} };
  await listener({ source, origin: 'null', data: { ...request, session: 'stale' } });
  await listener({ source: {}, origin: 'null', data: request });
  await listener({ source, origin: 'https://evil.example', data: request });
  assert.equal(requests.length, 0);
  await listener({ source, origin: 'null', data: request });
  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, '/api/app/storage');
  assert.equal(requests[0].options.headers.Authorization, 'Bearer scoped-secret');
  await listener({ source, origin: 'null', data: { ...request, id: 'two', operation: 'hub.settings' } });
  assert.equal(messages.at(-1).error.status, 403);
  await listener({ source, origin: 'null', data: { ...request, id: 'three', payload: { app_id: 'another' } } });
  assert.equal(messages.at(-1).error.status, 422);
  assert.equal(requests.length, 1);
  assert.equal(JSON.stringify(messages).includes('scoped-secret'), false);
  bridge.close();
  assert.equal(requests.at(-1).path, '/api/app/session');
  await listener({ source, origin: 'null', data: request });
  assert.equal(requests.length, 2);
});

test('a widget summary is published only with the grant, and never carries the token', async () => {
  let listener;
  globalThis.addEventListener = (_type, callback) => { listener = callback; };
  globalThis.removeEventListener = () => {};
  const messages = [], requests = [];
  const source = { postMessage: (message) => messages.push(message) };
  const open = (capabilities) => {
    messages.length = 0;
    requests.length = 0;
    createBridge({ frame: { contentWindow: source },
      session: { token: 'scoped-secret', installationId: 'installation-one', capabilities },
      context: { installationId: 'installation-one' },
      onReady() {}, onDirty() {}, onNavigate() {}, onError() {},
      fetcher: async (path, options) => { if (path !== '/api/app/features') requests.push({ path, options }); return new Response(JSON.stringify({ ok: true })); },
    });
    return listener({ source, origin: 'null', data: { type: 'vela:ready', protocol: 1 } });
  };
  const publish = (id, payload, session) => listener({ source, origin: 'null',
    data: { type: 'vela:request', protocol: 1, id, session, operation: 'widgets.publish', payload } });

  await open(['storage', 'widgets']);
  let nonce = messages[0].session;
  await publish('one', { id: 'sync', summary: { value: '73', unit: 'changes' } }, nonce);
  assert.equal(requests.at(-1).path, '/api/app/widgets/sync');
  assert.equal(requests.at(-1).options.method, 'PUT');
  assert.deepEqual(JSON.parse(requests.at(-1).options.body), { summary: { value: '73', unit: 'changes' } });
  // The app's bearer token stays in the host, exactly as for every other call.
  assert.equal(requests.at(-1).options.headers.Authorization, 'Bearer scoped-secret');
  assert.equal(JSON.stringify(messages).includes('scoped-secret'), false);

  // Nothing to report yet is a valid thing to say.
  await publish('two', { id: 'sync' }, nonce);
  assert.deepEqual(JSON.parse(requests.at(-1).options.body), { summary: {} });

  // Over the size cap, and unknown fields, are refused before any request.
  const sent = requests.length;
  await publish('three', { id: 'sync', summary: { caption: 'x'.repeat(5000) } }, nonce);
  assert.equal(messages.at(-1).error.status, 413);
  await publish('four', { id: 'sync', summary: {}, extra: true }, nonce);
  assert.equal(messages.at(-1).error.status, 422);
  await publish('five', { id: 7, summary: {} }, nonce);
  assert.equal(messages.at(-1).error.status, 422);
  assert.equal(requests.length, sent, 'a refused summary never reaches the engine');

  // Without the grant the host refuses it rather than letting the engine do so.
  await open(['storage']);
  nonce = messages[0].session;
  await publish('six', { id: 'sync', summary: { value: '1' } }, nonce);
  assert.equal(messages.at(-1).error.status, 403);
  assert.equal(requests.length, 0);
});

test('top bar items are granted, capped, and go down with the window', async () => {
  let listener;
  globalThis.addEventListener = (_type, callback) => { listener = callback; };
  globalThis.removeEventListener = () => {};
  const messages = [], requests = [], published = [];
  const source = { postMessage: (message) => messages.push(message) };
  const open = (capabilities) => {
    messages.length = 0;
    requests.length = 0;
    published.length = 0;
    const bridge = createBridge({ frame: { contentWindow: source },
      session: { token: 'scoped-secret', installationId: 'installation-one', capabilities },
      context: { installationId: 'installation-one' },
      onReady() {}, onDirty() {}, onNavigate() {}, onError() {},
      onTopBarItems: (items) => published.push(items),
      fetcher: async (path, options) => { if (path !== '/api/app/features') requests.push({ path, options }); return new Response(JSON.stringify({ ok: true })); },
    });
    return listener({ source, origin: 'null', data: { type: 'vela:ready', protocol: 1 } }).then(() => bridge);
  };
  const put = (id, payload, session) => listener({ source, origin: 'null',
    data: { type: 'vela:request', protocol: 1, id, session, operation: 'topbar.publish', payload } });

  const bridge = await open(['storage', 'topbar']);
  let nonce = messages[0].session;
  await put('one', { items: [{ id: 'temp', icon: 'thermometer', label: '-4°C', title: 'Oslo' }] }, nonce);
  assert.deepEqual(published.at(-1), [{ id: 'temp', icon: 'thermometer', label: '-4°C', title: 'Oslo' }]);
  // The items never travel: they belong to this window and this page draws them.
  assert.equal(requests.length, 0, 'nothing is sent to the engine');
  assert.equal(JSON.stringify(messages).includes('scoped-secret'), false);

  // Publishing replaces, and an empty list is how an app takes its items down.
  await put('two', { items: [] }, nonce);
  assert.deepEqual(published.at(-1), []);

  // Over the caps, unknown fields, and an icon this host does not have are all
  // refused before anything is kept.
  const kept = published.length;
  await put('three', { items: [{ id: 'a', label: 'x'.repeat(40) }] }, nonce);
  assert.equal(messages.at(-1).error.status, 422);
  await put('four', { items: [{ id: 'a', icon: 'https://evil.test/p.png' }] }, nonce);
  assert.equal(messages.at(-1).error.status, 422);
  await put('five', { items: [{ id: 'a', label: 'x', href: '/somewhere' }] }, nonce);
  assert.equal(messages.at(-1).error.status, 422);
  await put('six', { items: [{ id: 'a', label: '1' }, { id: 'b', label: '2' }, { id: 'c', label: '3' }, { id: 'd', label: '4' }] }, nonce);
  assert.equal(messages.at(-1).error.status, 422);
  await put('seven', { items: [{ id: 'a', icon: 'sun', title: 'x'.repeat(201) }] }, nonce);
  assert.equal(messages.at(-1).error.status, 422);
  await put('eight', { items: [], extra: true }, nonce);
  assert.equal(messages.at(-1).error.status, 422);
  assert.equal(published.length, kept, 'a refused list never reaches the bar');

  // Closing the window takes whatever it published with it, rather than
  // leaving an item that names an app nobody can reach any more.
  await put('nine', { items: [{ id: 'temp', label: '2°C' }] }, nonce);
  assert.equal(published.at(-1).length, 1);
  bridge.close();
  assert.deepEqual(published.at(-1), []);

  // Without the grant the host refuses it rather than drawing anything.
  await open(['storage']);
  nonce = messages[0].session;
  await put('ten', { items: [{ id: 'temp', label: '2°C' }] }, nonce);
  assert.equal(messages.at(-1).error.status, 403);
  assert.equal(published.length, 0);
});

test('hub worker never caches authenticated API traffic and retires old API caches', async () => {
  const { readFile } = await import('node:fs/promises');
  const { runInNewContext } = await import('node:vm');
  const handlers = {}, removed = [];
  const keep = ['vela-shell-v2', 'vela-runtime-v2', 'vela-health-1.0.0'];
  const self = { location: { origin: 'http://localhost' }, addEventListener: (type, callback) => { handlers[type] = callback; }, clients: { claim() {} } };
  runInNewContext(await readFile(new URL('../web/public/sw.js', import.meta.url), 'utf8'), {
    self, URL, caches: { keys: async () => [...keep, 'vela-api-v1', 'vela-runtime-v1'], delete: async key => removed.push(key) },
  });
  let activation;
  handlers.activate({ waitUntil: (promise) => { activation = promise; } });
  await activation;
  assert.deepEqual(removed.sort(), ['vela-api-v1', 'vela-runtime-v1']);
  for (const path of ['/api/session', '/api/app/storage', '/api/settings']) {
    let intercepted = false;
    handlers.fetch({ request: { method: 'GET', url: 'http://localhost' + path }, respondWith: () => { intercepted = true; } });
    assert.equal(intercepted, false);
  }
});

test('a change waiting for approval outlasts the ten-second reply timeout and lands exactly once', async () => {
  let listener;
  globalThis.addEventListener = (_type, callback) => {
    listener = callback;
  };
  globalThis.removeEventListener = () => {};
  const messages = [],
    requests = [];
  const source = { postMessage: (message) => messages.push(message) };
  // The owner answers after the bridge has asked about the request a few times,
  // which is the point of the test: an app's ten seconds is not how long a
  // person takes, and the host is the one that waits.
  let looks = 0;
  let writes = 0;
  const bridge = createBridge({
    frame: { contentWindow: source },
    session: { token: 'scoped-secret', installationId: 'installation-one', capabilities: ['storage'] },
    context: { installationId: 'installation-one' },
    onReady() {},
    onDirty() {},
    onNavigate() {},
    onError() {},
    fetcher: async (path, options) => {
      requests.push({ path, options });
      if (path === '/api/app/storage' && options?.method === 'PUT') {
        writes += 1;
        if (writes === 1) {
          return new Response(
            JSON.stringify({
              pending: {
                requestId: 'req-1',
                state: 'pending',
                effect: 'write',
                expiresAt: Date.now() / 1000 + 300,
                summary: { headline: 'Notes wants to save a change.', detail: ['one more note'] },
              },
              detail: 'Notes wants to save a change.',
            }),
            { status: 202 },
          );
        }
        return new Response(JSON.stringify({ revision: 2, value: { notes: ['one'] } }));
      }
      if (path.startsWith('/api/app/approvals/req-1')) {
        looks += 1;
        return new Response(
          JSON.stringify({
            requestId: 'req-1',
            state: looks >= 3 ? 'approved' : 'pending',
            expiresAt: Date.now() / 1000 + 300,
          }),
        );
      }
      return new Response(JSON.stringify({ ok: true }));
    },
  });
  await listener({ source, origin: 'null', data: { type: 'vela:ready', protocol: 1, features: ['approvals'] } });
  const nonce = messages[0].session;
  const answer = listener({
    source,
    origin: 'null',
    data: {
      type: 'vela:request',
      protocol: 1,
      id: 'w1',
      session: nonce,
      operation: 'storage.write',
      payload: { value: { notes: ['one'] }, revision: 1 },
    },
  });
  await answer;

  // The app was told it is waiting, not that it failed.
  const told = messages.find((message) => message.type === 'vela:pending');
  assert.ok(told, 'the app is told a person has been asked');
  assert.equal(told.id, 'w1');
  assert.equal(told.request.summary.headline, 'Notes wants to save a change.');

  // And then it got its real answer, from one write rather than two.
  const reply = messages.filter((message) => message.type === 'vela:response' && message.id === 'w1');
  assert.equal(reply.length, 1);
  assert.equal(reply[0].error, undefined, JSON.stringify(reply[0]));
  assert.equal(reply[0].result.revision, 2);
  assert.equal(writes, 2, 'asked once, retried once — the effect itself happened once');
  assert.equal(JSON.stringify(messages).includes('scoped-secret'), false);
  bridge.close();
});

test('an app that cannot wait is told so, and the question is withdrawn rather than left open', async () => {
  let listener;
  globalThis.addEventListener = (_type, callback) => {
    listener = callback;
  };
  globalThis.removeEventListener = () => {};
  const messages = [],
    requests = [];
  const source = { postMessage: (message) => messages.push(message) };
  createBridge({
    frame: { contentWindow: source },
    session: { token: 'scoped-secret', installationId: 'installation-one', capabilities: ['storage'] },
    context: { installationId: 'installation-one' },
    onReady() {},
    onDirty() {},
    onNavigate() {},
    onError() {},
    fetcher: async (path, options) => {
      requests.push({ path, options });
      if (path === '/api/app/storage' && options?.method === 'PUT')
        return new Response(
          JSON.stringify({
            pending: {
              requestId: 'req-2',
              state: 'pending',
              effect: 'write',
              expiresAt: Date.now() / 1000 + 300,
              summary: { headline: 'Notes wants to save a change.', detail: [] },
            },
          }),
          { status: 202 },
        );
      return new Response(JSON.stringify({ ok: true }));
    },
  });
  // An older SDK announces no features at all.
  await listener({ source, origin: 'null', data: { type: 'vela:ready', protocol: 1 } });
  const nonce = messages[0].session;
  await listener({
    source,
    origin: 'null',
    data: {
      type: 'vela:request',
      protocol: 1,
      id: 'w2',
      session: nonce,
      operation: 'storage.write',
      payload: { value: { notes: [] }, revision: 0 },
    },
  });
  const reply = messages.at(-1);
  assert.equal(reply.type, 'vela:response');
  assert.equal(reply.error.status, 403);
  assert.match(reply.error.message, /cannot wait for one/);
  // Waiting would have been worse than saying so, and a prompt nobody is behind
  // any more does not stay on the owner's screen.
  assert.ok(
    requests.some((request) => request.path === '/api/app/approvals/req-2/abandon'),
    'the question is withdrawn',
  );
});

test('an app opens links only on the site of its http connection', async () => {
  let listener;
  globalThis.addEventListener = (_type, callback) => { listener = callback; };
  globalThis.removeEventListener = () => {};
  const opened = [];
  globalThis.window = { open: (url, target, features) => opened.push({ url, target, features }) };
  const messages = [];
  const source = { postMessage: (message) => messages.push(message) };
  createBridge({ frame: { contentWindow: source },
    session: { token: 'scoped-secret', installationId: 'installation-one', capabilities: ['connections'] },
    context: { installationId: 'installation-one' },
    onReady() {}, onDirty() {}, onNavigate() {}, onError() {},
    fetcher: async () => new Response(JSON.stringify({ provider: 'http', endpoint: 'https://api.github.com' })),
  });
  await listener({ source, origin: 'null', data: { type: 'vela:ready', protocol: 1 } });
  const nonce = messages[0].session;
  const open = async (url, id) => {
    await listener({ source, origin: 'null', data: { type: 'vela:request', protocol: 1, id, session: nonce, operation: 'navigation.open', payload: { url } } });
    return messages.at(-1);
  };
  assert.equal((await open('https://github.com/jhd3197/vela/pull/1', 'a')).result.ok, true);
  assert.equal((await open('https://gist.github.com/x', 'b')).result.ok, true);
  for (const [url, id] of [['https://evil.example/github.com', 'c'], ['http://github.com/x', 'd'], ['https://github.com.evil.example/', 'e'], ['https://user:pass@github.com/', 'f'], ['javascript:alert(1)', 'g']]) {
    assert.ok((await open(url, id)).error, url);
  }
  assert.deepEqual(opened.map((entry) => entry.url), ['https://github.com/jhd3197/vela/pull/1', 'https://gist.github.com/x']);
  assert.equal(opened[0].features, 'noopener,noreferrer');
  delete globalThis.window;
});
