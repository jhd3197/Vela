import assert from 'node:assert/strict';
import test from 'node:test';
import { createResource } from '../web/src/hooks/resource.js';

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test('manual refreshes join a pending request instead of duplicating it', async () => {
  const request = deferred();
  let calls = 0, state;
  const resource = createResource(() => { calls++; return request.promise; }, { onChange: next => { state = next; } });
  const first = resource.refresh();
  const second = resource.refresh();
  assert.equal(first, second);
  await Promise.resolve();
  assert.equal(calls, 1);
  assert.equal(state.refreshing, true);
  request.resolve({ name: 'Health' });
  assert.deepEqual(await first, { name: 'Health' });
  assert.deepEqual(state, { data: { name: 'Health' }, error: null, loading: false, refreshing: false });
  resource.dispose();
});

test('polling waits until the previous request completes', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const request = deferred();
  let calls = 0;
  const resource = createResource(() => { calls++; return request.promise; }, { onChange() {}, intervalMs: 100 });
  t.after(() => resource.dispose());
  const first = resource.refresh();
  await Promise.resolve();
  t.mock.timers.tick(1000);
  assert.equal(calls, 1);
  request.resolve('ready');
  await first;
  t.mock.timers.tick(99);
  await Promise.resolve();
  assert.equal(calls, 1);
  t.mock.timers.tick(1);
  await Promise.resolve();
  assert.equal(calls, 2);
});

test('failures retain the last data and a successful retry clears the error', async () => {
  let fail = false, state;
  const resource = createResource(() => {
    if (fail) throw new Error('offline');
    return 'last good data';
  }, { onChange: next => { state = next; } });
  await resource.refresh();
  fail = true;
  assert.equal(await resource.refresh(), undefined);
  assert.equal(state.data, 'last good data');
  assert.equal(state.error.message, 'offline');
  assert.equal(state.loading, false);
  assert.equal(state.refreshing, false);
  fail = false;
  await resource.refresh();
  assert.equal(state.error, null);
  resource.dispose();
});

test('disposing aborts the request and prevents late data from publishing', async () => {
  const request = deferred();
  let signal;
  const updates = [];
  const resource = createResource(options => {
    signal = options.signal;
    return request.promise;
  }, { onChange: state => updates.push(state) });
  const pending = resource.refresh();
  await Promise.resolve();
  resource.dispose();
  assert.equal(signal.aborted, true);
  const count = updates.length;
  request.resolve('old app');
  assert.equal(await pending, undefined);
  assert.equal(updates.length, count);
  assert.equal(await resource.refresh(), undefined);
});

test('disposing before the loader starts avoids a request, including strict-mode cleanup', async () => {
  let calls = 0;
  const resource = createResource(() => { calls++; }, { onChange() {} });
  const pending = resource.refresh();
  resource.dispose();
  await pending;
  assert.equal(calls, 0);
});

test('disposing clears scheduled polls and late rejections are handled', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let calls = 0;
  const resource = createResource(() => { calls++; return 'ready'; }, { onChange() {}, intervalMs: 100 });
  await resource.refresh();
  resource.dispose();
  t.mock.timers.tick(1000);
  await Promise.resolve();
  assert.equal(calls, 1);

  const request = deferred();
  const updates = [];
  const other = createResource(() => request.promise, { onChange: state => updates.push(state) });
  const pending = other.refresh();
  await Promise.resolve();
  other.dispose();
  const count = updates.length;
  request.reject(new Error('late failure'));
  await pending;
  assert.equal(updates.length, count);
});
