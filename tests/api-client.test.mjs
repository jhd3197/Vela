// In-flight GET coalescing in the dashboard's API client.
//
// `fetch` is injected by replacing the global, which is what `web/src/api.js`
// resolves at call time; nothing test-only is exported from the client itself.
import assert from 'node:assert/strict';
import test from 'node:test';

import { acceptHubSession, hubFetch } from '../web/src/api.js';

const JSON_GET = { headers: { Accept: 'application/json' } };

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

// Let every pending microtask and timer callback run, so the client has
// reached its `fetch` call before a test inspects the calls it made.
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

// A `fetch` whose every call is held open until the test answers it. The
// session bootstrap answers itself: no test here is about the token dance.
function stubFetch() {
  const calls = [];
  globalThis.fetch = (path, options = {}) => {
    if (path === '/api/session') return Promise.resolve(json({ token: 'fresh-token' }));
    const call = { path, options, aborted: false };
    call.promise = new Promise((resolve, reject) => {
      call.resolve = resolve;
      call.reject = reject;
    });
    options.signal?.addEventListener(
      'abort',
      () => {
        call.aborted = true;
        call.reject(options.signal.reason);
      },
      { once: true },
    );
    calls.push(call);
    return call.promise;
  };
  return calls;
}

test.beforeEach(() => {
  acceptHubSession('hub-token');
});

test('two concurrent identical GETs make one request and get their own body', async () => {
  const calls = stubFetch();
  const first = hubFetch('/api/engine', JSON_GET);
  const second = hubFetch('/api/engine', JSON_GET);
  await settle();

  assert.equal(calls.length, 1, 'the second caller joined the first request');
  calls[0].resolve(json({ apps_running: 2 }));

  const bodies = await Promise.all((await Promise.all([first, second])).map((r) => r.json()));
  assert.deepEqual(bodies[0], { apps_running: 2 });
  assert.deepEqual(bodies[1], { apps_running: 2 });
  assert.notEqual(bodies[0], bodies[1], 'one caller mutating its result cannot reach the other');
});

test('the entry dies on settlement, so a later GET fetches again', async () => {
  const calls = stubFetch();
  const first = hubFetch('/api/engine', JSON_GET);
  await settle();
  calls[0].resolve(json({ apps_running: 1 }));
  assert.deepEqual(await (await first).json(), { apps_running: 1 });

  const second = hubFetch('/api/engine', JSON_GET);
  await settle();
  assert.equal(calls.length, 2, 'a settled request is never joined');
  calls[1].resolve(json({ apps_running: 5 }));
  assert.deepEqual(await (await second).json(), { apps_running: 5 });
});

test('different paths are never shared', async () => {
  const calls = stubFetch();
  hubFetch('/api/engine', JSON_GET);
  hubFetch('/api/settings', JSON_GET);
  await settle();
  assert.deepEqual(
    calls.map((call) => call.path),
    ['/api/engine', '/api/settings'],
  );
  calls.forEach((call) => call.resolve(json({})));
});

test('a POST is never shared', async () => {
  const calls = stubFetch();
  hubFetch('/api/updates/check', { method: 'POST' });
  hubFetch('/api/updates/check', { method: 'POST' });
  await settle();
  assert.equal(calls.length, 2);
  calls.forEach((call) => call.resolve(json({})));
});

test('a GET carrying a body or a header of its own is never shared', async () => {
  const calls = stubFetch();
  hubFetch('/api/logs', { headers: { Accept: 'application/json', 'X-Vela-Confirm': 'clear' } });
  hubFetch('/api/logs', { headers: { Accept: 'application/json', 'X-Vela-Confirm': 'clear' } });
  hubFetch('/api/search', { body: 'q=a' });
  hubFetch('/api/search', { body: 'q=a' });
  await settle();
  assert.equal(calls.length, 4);
  calls.forEach((call) => call.resolve(json({})));
});

test('a credential check keeps its own request and its own 401', async () => {
  const calls = stubFetch();
  hubFetch('/api/security/verify', { ...JSON_GET, retryUnauthorized: false });
  hubFetch('/api/security/verify', { ...JSON_GET, retryUnauthorized: false });
  await settle();
  assert.equal(calls.length, 2, 'two attempts at a credential are two attempts');

  calls[0].resolve(json({ detail: 'no' }, 401));
  calls[1].resolve(json({ detail: 'no' }, 401));
  await settle();
  assert.equal(calls.length, 2, 'and neither of them is retried');
});

test('a 401 is retried once inside the shared request and serves both callers', async () => {
  const calls = stubFetch();
  const first = hubFetch('/api/engine', JSON_GET);
  const second = hubFetch('/api/engine', JSON_GET);
  await settle();
  assert.equal(calls.length, 1);

  calls[0].resolve(json({ detail: 'stale' }, 401));
  await settle();
  assert.equal(calls.length, 2, 'one retry, not one per caller');
  assert.equal(
    calls[1].options.headers.get('Authorization'),
    'Bearer fresh-token',
    'the retry carries the token fetched after the 401',
  );

  calls[1].resolve(json({ apps_running: 7 }));
  const [a, b] = await Promise.all([first, second]);
  assert.equal(a.status, 200);
  assert.equal(b.status, 200);
  assert.deepEqual(await a.json(), { apps_running: 7 });
  assert.deepEqual(await b.json(), { apps_running: 7 });
});

test('one caller aborting leaves the other caller and the request alone', async () => {
  const calls = stubFetch();
  const leaving = new AbortController();
  const staying = hubFetch('/api/engine', JSON_GET);
  const going = hubFetch('/api/engine', { ...JSON_GET, signal: leaving.signal });
  await settle();
  assert.equal(calls.length, 1);

  leaving.abort();
  await assert.rejects(going, (error) => error.name === 'AbortError');
  assert.equal(calls[0].aborted, false, 'the shared request still has a waiter');

  calls[0].resolve(json({ apps_running: 3 }));
  assert.deepEqual(await (await staying).json(), { apps_running: 3 });
});

test('the shared request is abandoned once every caller has gone', async () => {
  const calls = stubFetch();
  const one = new AbortController();
  const two = new AbortController();
  const first = hubFetch('/api/engine', { ...JSON_GET, signal: one.signal });
  const second = hubFetch('/api/engine', { ...JSON_GET, signal: two.signal });
  await settle();
  assert.equal(calls.length, 1);

  one.abort();
  await assert.rejects(first, (error) => error.name === 'AbortError');
  assert.equal(calls[0].aborted, false);

  two.abort();
  await assert.rejects(second, (error) => error.name === 'AbortError');
  assert.equal(calls[0].aborted, true, 'nobody is waiting, so the request stops');

  // And the abandoned entry is not handed to whoever asks next.
  hubFetch('/api/engine', JSON_GET);
  await settle();
  assert.equal(calls.length, 2);
  calls[1].resolve(json({}));
});

test('a caller whose signal is already aborted never starts a request', async () => {
  const calls = stubFetch();
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(
    hubFetch('/api/engine', { ...JSON_GET, signal: controller.signal }),
    (error) => error.name === 'AbortError',
  );
  await settle();
  assert.equal(calls.length, 0);
});
