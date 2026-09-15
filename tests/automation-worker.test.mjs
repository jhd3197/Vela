/**
 * The automation worker's protocol and its executor allow-list.
 *
 * These checks run the real worker process over its real stdio protocol. They
 * skip with a reason when its dependencies have not been installed, so a fresh
 * clone still reports honestly instead of failing for the wrong reason.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const workerScript = path.join(root, 'scripts/automation-worker/src/index.mjs');
const installed = existsSync(path.join(root, 'scripts/automation-worker/node_modules/@tramo/runtime'));
const skip = installed
  ? false
  : 'run python scripts/setup-automation-worker.py to install the automation runtime';

const { createLineReader, encode, MAX_MESSAGE_BYTES } = await import(
  new URL('../scripts/automation-worker/src/protocol.mjs', import.meta.url),
);

/** Drive one worker process and collect everything it says. */
function session(env = {}) {
  const child = spawn(process.execPath, [workerScript], {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, ...env },
  });
  const handlers = [];
  const messages = [];
  const stderr = [];
  child.stderr.on('data', (chunk) => stderr.push(String(chunk)));
  const read = createLineReader(
    (message) => {
      messages.push(message);
      for (const handler of handlers.slice()) {
        if (handler.match(message)) {
          handlers.splice(handlers.indexOf(handler), 1);
          handler.resolve(message);
        }
      }
    },
    (detail) => stderr.push(detail),
  );
  child.stdout.setEncoding('utf8');
  child.stdout.on('data', read);

  const state = { exited: null };
  child.once('exit', (code) => {
    state.exited = code ?? 0;
  });

  return {
    messages,
    stderr,
    get exited() {
      return state.exited;
    },
    send: (message) => child.stdin.write(encode(message)),
    raw: (line) => child.stdin.write(line),
    await: (match, seconds = 15) =>
      new Promise((resolve, reject) => {
        const existing = messages.find(match);
        if (existing) return resolve(existing);
        const timer = setTimeout(
          () => reject(new Error(`timed out waiting for a message; stderr: ${stderr.join('')}`)),
          seconds * 1000,
        );
        handlers.push({
          match,
          resolve: (message) => {
            clearTimeout(timer);
            resolve(message);
          },
        });
        return undefined;
      }),
    stop: () =>
      new Promise((resolve) => {
        child.once('exit', resolve);
        child.kill();
      }),
  };
}

const doc = (nodes, edges = []) => ({ version: 1, nodes, edges, meta: {} });

test('the line reader refuses oversized and malformed input without growing', () => {
  const seen = [];
  const errors = [];
  const read = createLineReader(
    (message) => seen.push(message),
    (detail) => errors.push(detail),
  );
  read('{"v":1,"t":"ping","id":"a"}\n');
  read('not json\n');
  read('{"v":99,"t":"ping"}\n');
  read(`${'x'.repeat(MAX_MESSAGE_BYTES + 10)}\n`);
  read('{"v":1,"t":"ping","id":"b"}\n');
  assert.deepEqual(
    seen.map((message) => message.id),
    ['a', 'b'],
  );
  assert.equal(errors.length, 3);
  assert.throws(() => encode({ blob: 'x'.repeat(MAX_MESSAGE_BYTES + 1) }), /exceeds/);
});

test('the worker announces only the steps Vela vets', { skip }, async () => {
  const worker = session();
  const ready = await worker.await((message) => message.t === 'ready');
  assert.equal(ready.protocol, 1);
  for (const id of ['manual-trigger', 'template', 'log', 'vela-condition', 'vela-app-action']) {
    assert.ok(ready.nodes.includes(id), `expected ${id}`);
  }
  for (const id of ['js-transform', 'if', 'switch', 'http-request', 'mcp-tool-call', 'ai-prompt']) {
    assert.ok(!ready.nodes.includes(id), `${id} must not be executable`);
  }
  worker.send({ v: 1, t: 'ping', id: 'p1' });
  assert.equal((await worker.await((message) => message.t === 'pong')).id, 'p1');
  await worker.stop();
});

test('an excluded step cannot run even when it reaches the worker', { skip }, async () => {
  const worker = session();
  await worker.await((message) => message.t === 'ready');
  worker.send({
    v: 1,
    t: 'run',
    id: 'r1',
    runId: 'run-excluded',
    doc: doc([{ id: 'n1', type: 'js-transform', config: { expression: 'return 1' } }]),
    limits: { deadlineMs: 5000 },
  });
  const result = await worker.await((message) => message.t === 'result');
  const errors = worker.messages.filter(
    (message) => message.t === 'event' && message.event.type === 'node-error',
  );
  assert.ok(errors.length > 0 || !result.ok, 'the run must not report success');
  await worker.stop();
});

test('a branch compares a field without evaluating it', { skip }, async () => {
  const worker = session();
  await worker.await((message) => message.t === 'ready');
  worker.send({
    v: 1,
    t: 'run',
    id: 'r1',
    runId: 'run-branch',
    doc: doc(
      [
        { id: 'start', type: 'manual-trigger', config: { payload: '{"status":"ready"}' } },
        {
          id: 'check',
          type: 'vela-condition',
          // A value that would be code if anything evaluated it.
          config: {
            subject: 'status',
            operator: 'equals',
            value: 'ready',
            valueType: 'string',
          },
        },
        { id: 'yes', type: 'log', config: { level: 'info', prefix: 'matched' } },
        { id: 'no', type: 'log', config: { level: 'info', prefix: 'missed' } },
      ],
      [
        { id: 'e1', source: 'start', target: 'check' },
        { id: 'e2', source: 'check', target: 'yes', sourceHandle: 'true' },
        { id: 'e3', source: 'check', target: 'no', sourceHandle: 'false' },
      ],
    ),
    limits: { deadlineMs: 5000 },
  });
  await worker.await((message) => message.t === 'result');
  const succeeded = worker.messages
    .filter((message) => message.t === 'event' && message.event.type === 'node-success')
    .map((message) => message.event.nodeId);
  assert.ok(succeeded.includes('yes'));
  assert.ok(!succeeded.includes('no'));
  await worker.stop();
});

test('an effect waits for the host and names its run', { skip }, async () => {
  const worker = session();
  await worker.await((message) => message.t === 'ready');
  worker.send({
    v: 1,
    t: 'run',
    id: 'r1',
    runId: 'run-effect',
    doc: doc([
      { id: 'tell', type: 'vela-notify', config: { title: 'Hi', message: 'There', priority: 3 } },
    ]),
    limits: { deadlineMs: 5000 },
  });
  const request = await worker.await((message) => message.t === 'effect');
  assert.equal(request.kind, 'notify');
  assert.equal(request.runId, 'run-effect');
  assert.equal(request.nodeId, 'tell');
  assert.deepEqual(request.payload, { title: 'Hi', message: 'There', priority: 3 });
  // Refusing the effect must fail the step rather than continuing quietly.
  worker.send({ v: 1, t: 'effect-result', id: request.id, ok: false, error: 'not allowed' });
  await worker.await((message) => message.t === 'result');
  const errors = worker.messages.filter(
    (message) => message.t === 'event' && message.event.type === 'node-error',
  );
  assert.equal(errors.length, 1);
  assert.match(errors[0].event.error, /not allowed/);
  await worker.stop();
});

test('cancelling stops a waiting run', { skip }, async () => {
  const worker = session();
  await worker.await((message) => message.t === 'ready');
  worker.send({
    v: 1,
    t: 'run',
    id: 'r1',
    runId: 'run-cancel',
    doc: doc([
      { id: 'start', type: 'manual-trigger', config: { payload: '{}' } },
      { id: 'wait', type: 'delay', config: { ms: 60000 } },
    ], [{ id: 'e1', source: 'start', target: 'wait' }]),
    limits: { deadlineMs: 120000 },
  });
  await worker.await(
    (message) => message.t === 'event' && message.event.type === 'node-start' && message.event.nodeId === 'wait',
  );
  worker.send({ v: 1, t: 'cancel', id: 'c1', runId: 'run-cancel' });
  const cancelled = await worker.await((message) => message.t === 'cancelled');
  assert.equal(cancelled.found, true);
  const result = await worker.await((message) => message.t === 'result');
  assert.equal(result.cancelled, true);
  await worker.stop();
});

test('a run past its deadline is reported as a timeout', { skip }, async () => {
  const worker = session();
  await worker.await((message) => message.t === 'ready');
  worker.send({
    v: 1,
    t: 'run',
    id: 'r1',
    runId: 'run-timeout',
    doc: doc([
      { id: 'start', type: 'manual-trigger', config: { payload: '{}' } },
      { id: 'wait', type: 'delay', config: { ms: 30000 } },
    ], [{ id: 'e1', source: 'start', target: 'wait' }]),
    limits: { deadlineMs: 300 },
  });
  const result = await worker.await((message) => message.t === 'result');
  assert.equal(result.status, 'timeout');
  assert.equal(result.ok, false);
  await worker.stop();
});

test('the worker exits when Vela goes silent', { skip }, async () => {
  // Vela pings while it is alive. A server that was force-killed stops pinging,
  // and the worker must not keep running with no one to talk to.
  const worker = session({ VELA_WORKER_SILENCE_MS: '1200' });
  await worker.await((message) => message.t === 'ready');
  const exited = await new Promise((resolve) => {
    const started = Date.now();
    const timer = setInterval(() => {
      if (worker.exited !== null) {
        clearInterval(timer);
        resolve(Date.now() - started);
      }
    }, 100);
  });
  assert.ok(exited < 8000, `worker lingered for ${exited}ms`);
});

test('closing the pipe stops the worker rather than orphaning it', { skip }, async () => {
  const child = spawn(process.execPath, [workerScript], { stdio: ['pipe', 'pipe', 'ignore'] });
  await new Promise((resolve) => child.stdout.once('data', resolve));
  child.stdin.end();
  const code = await new Promise((resolve) => child.once('exit', resolve));
  assert.equal(code, 0);
});
