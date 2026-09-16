import test from 'node:test';
import assert from 'node:assert/strict';
import { createBridge } from '../web/src/bridge/host.js';

test('bridge binds source, opaque origin, protocol, nonce and operation; tokens stay in host', async () => {
  let listener;
  globalThis.addEventListener = (_type, callback) => { listener = callback; };
  globalThis.removeEventListener = () => {};
  const messages = [], requests = [];
  const source = { postMessage: (message) => messages.push(message) };
  let ready = false;
  const bridge = createBridge({ frame: { contentWindow: source },
    session: { token: 'scoped-secret', installationId: 'installation-one', capabilities: ['storage'] },
    context: { installationId: 'installation-one' },
    onReady: () => { ready = true; }, onDirty() {}, onNavigate() {}, onError() {},
    fetcher: async (path, options) => { requests.push({ path, options }); return new Response(JSON.stringify({ revision: 1, value: 'own data' })); },
  });
  const hello = { type: 'vela:ready', protocol: 1 };
  await listener({ source: {}, origin: 'null', data: hello });
  await listener({ source, origin: 'https://evil.example', data: hello });
  await listener({ source, origin: 'null', data: { ...hello, protocol: 9 } });
  assert.equal(ready, false);
  await listener({ source, origin: 'null', data: hello });
  assert.equal(ready, true);
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
      fetcher: async (path, options) => { requests.push({ path, options }); return new Response(JSON.stringify({ ok: true })); },
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
