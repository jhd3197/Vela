/**
 * Vela automation worker.
 *
 * Vela starts this process, keeps it on a private stdio pipe and stops it. It
 * executes one approved workflow revision at a time with `@tramo/runtime`,
 * reports node events as they happen, and asks the host to perform every side
 * effect. It holds no Vela credential, opens no port and reads no Vela data
 * directory.
 */

import process from 'node:process';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { run } from '@tramo/runtime';
import { createLineReader, encode, PROTOCOL_VERSION, MAX_MESSAGE_BYTES } from './protocol.mjs';
import { createVelaRegistry, supportedNodeIds } from './executors.mjs';

function packageVersion(name) {
  // The Tramo packages declare an import-only `exports` map, so neither
  // `require.resolve(name)` nor `require(name + '/package.json')` works. Read
  // the manifest from the dependency tree installed beside this worker, then
  // fall back to walking up from the resolved entry point.
  const candidates = [new URL(`../node_modules/${name}/package.json`, import.meta.url)];
  try {
    let directory = dirname(fileURLToPath(import.meta.resolve(name)));
    for (let depth = 0; depth < 5; depth += 1) {
      candidates.push(new URL(`file://${join(directory, 'package.json').replace(/\\/g, '/')}`));
      directory = dirname(directory);
    }
  } catch {
    /* The resolver is optional; the installed path above is the normal case. */
  }
  for (const candidate of candidates) {
    try {
      const path = fileURLToPath(candidate);
      if (existsSync(path)) return JSON.parse(readFileSync(path, 'utf8')).version;
    } catch {
      /* Try the next candidate. */
    }
  }
  return 'unknown';
}

/* ---------------------------------------------------------------- output -- */

let writeFailed = false;

function send(message) {
  if (writeFailed) return;
  try {
    process.stdout.write(encode(message));
  } catch (error) {
    writeFailed = true;
    diagnostic(`failed to write a protocol message: ${error.message}`);
  }
}

function diagnostic(text) {
  try {
    process.stderr.write(`[vela-automation-worker] ${text}\n`);
  } catch {
    /* A closed stderr must not take the run down. */
  }
}

/** Errors leaving the worker are messages, never stack traces or file paths. */
function safeMessage(error) {
  const text = error && error.message ? String(error.message) : String(error ?? 'unknown error');
  return text.replace(/\s+/g, ' ').slice(0, 800);
}

/* ------------------------------------------------------------- host calls -- */

let nextEffectId = 0;
const pendingEffects = new Map();

const host = {
  /** Ask Vela to perform one authorized side effect and wait for its receipt. */
  effect(request, signal) {
    return new Promise((resolve, reject) => {
      if (signal?.aborted) {
        reject(new Error('run was cancelled before the step could start'));
        return;
      }
      const id = `e${++nextEffectId}`;
      const onAbort = () => {
        pendingEffects.delete(id);
        reject(new Error('run was cancelled while waiting for Vela'));
      };
      signal?.addEventListener('abort', onAbort, { once: true });
      pendingEffects.set(id, {
        resolve: (value) => {
          signal?.removeEventListener('abort', onAbort);
          resolve(value);
        },
        reject: (error) => {
          signal?.removeEventListener('abort', onAbort);
          reject(error);
        },
      });
      send({ v: PROTOCOL_VERSION, t: 'effect', id, ...request });
    });
  },
};

function settleEffect(message) {
  const pending = pendingEffects.get(message.id);
  if (!pending) {
    diagnostic(`ignored a result for unknown request ${message.id}`);
    return;
  }
  pendingEffects.delete(message.id);
  if (message.ok) pending.resolve(message.output ?? null);
  else pending.reject(new Error(String(message.error ?? 'Vela refused the step')));
}

/* ------------------------------------------------------------------ runs -- */

const activeRuns = new Map();

async function startRun(message) {
  const { id, runId } = message;
  if (activeRuns.has(runId)) {
    send({ v: PROTOCOL_VERSION, t: 'result', id, runId, ok: false, status: 'failed', error: 'run is already in progress' });
    return;
  }
  const controller = new AbortController();
  const limits = message.limits ?? {};
  const deadlineMs = Number(limits.deadlineMs ?? 0);
  let expired = false;
  const timer =
    deadlineMs > 0
      ? setTimeout(() => {
          expired = true;
          controller.abort();
        }, deadlineMs)
      : null;
  activeRuns.set(runId, controller);

  const registry = createVelaRegistry(host, runId);

  // The scheduler skips a node it has no executor for, which would let a run
  // with an unsupported step report success having done nothing. Vela's server
  // already refuses such a document; refuse it here too rather than rely on one
  // side of the boundary.
  const unknown = [
    ...new Set((message.doc?.nodes ?? []).filter((n) => !registry.get(n.type)).map((n) => n.type)),
  ];
  if (unknown.length) {
    activeRuns.delete(runId);
    if (timer) clearTimeout(timer);
    send({
      v: PROTOCOL_VERSION,
      t: 'result',
      id,
      runId,
      ok: false,
      status: 'failed',
      error: `this workflow uses steps this runtime cannot run: ${unknown.join(', ')}`,
      pending: [],
      checkpoint: null,
      cancelled: false,
    });
    return;
  }

  let lastCheckpoint = message.resume ?? undefined;

  try {
    const result = await run(message.doc, registry, {
      trigger: message.trigger,
      signal: controller.signal,
      concurrency: Number(limits.concurrency ?? 1) || 1,
      approvals: message.approvals ?? undefined,
      resumeFrom: message.resume ?? undefined,
      checkpoint: (state) => {
        lastCheckpoint = state;
      },
      // Vela keeps the run log; the worker's own logger stays on stderr.
      logger: undefined,
      // Re-stamp the runner's internal id with Vela's durable run id so the
      // host never has to correlate two identifiers for one run.
      onEvent: (event) => send({ v: PROTOCOL_VERSION, t: 'event', runId, event: { ...event, runId } }),
    });
    send({
      v: PROTOCOL_VERSION,
      t: 'result',
      id,
      runId,
      // A run stopped by its deadline never reports success, whatever the
      // scheduler managed to finish first.
      ok: result.ok && !expired,
      status: expired ? 'timeout' : (result.status ?? 'completed'),
      error: expired ? 'the run exceeded its time limit' : (result.error ?? null),
      pending: result.pendingApprovals ?? [],
      checkpoint: result.checkpoint ?? lastCheckpoint ?? null,
      cancelled: controller.signal.aborted && !expired,
    });
  } catch (error) {
    send({
      v: PROTOCOL_VERSION,
      t: 'result',
      id,
      runId,
      ok: false,
      status: expired ? 'timeout' : 'failed',
      error: safeMessage(error),
      pending: [],
      checkpoint: lastCheckpoint ?? null,
      cancelled: controller.signal.aborted && !expired,
    });
  } finally {
    if (timer) clearTimeout(timer);
    activeRuns.delete(runId);
    for (const [effectId, pending] of pendingEffects) {
      pendingEffects.delete(effectId);
      pending.reject(new Error('the run ended before Vela answered'));
    }
  }
}

/* -------------------------------------------------------------- dispatch -- */

function handle(message) {
  switch (message.t) {
    case 'run':
      startRun(message).catch((error) => diagnostic(`run dispatch failed: ${safeMessage(error)}`));
      return;
    case 'cancel': {
      const controller = activeRuns.get(message.runId);
      if (controller) controller.abort();
      send({ v: PROTOCOL_VERSION, t: 'cancelled', id: message.id, runId: message.runId, found: Boolean(controller) });
      return;
    }
    case 'effect-result':
      settleEffect(message);
      return;
    case 'ping':
      send({ v: PROTOCOL_VERSION, t: 'pong', id: message.id });
      return;
    case 'shutdown':
      for (const controller of activeRuns.values()) controller.abort();
      send({ v: PROTOCOL_VERSION, t: 'stopping' });
      // Give in-flight results a tick to flush before the pipe closes.
      setTimeout(() => process.exit(0), 50);
      return;
    default:
      diagnostic(`ignored unknown message type "${message.t}"`);
  }
}

/* ------------------------------------------------------------- watchdog --
 *
 * If Vela is force-killed, the pipe does not always break in a way this process
 * notices, and an orphan would keep running with no one to talk to. Vela pings
 * on a fixed interval while it is alive, so silence past the limit below means
 * it is gone. This is the backstop; the stdin close handler is the normal path.
 */

const SILENCE_LIMIT_MS = Math.max(1000, Number(process.env.VELA_WORKER_SILENCE_MS) || 90_000);
let lastHeard = Date.now();

const watchdog = setInterval(() => {
  if (Date.now() - lastHeard < SILENCE_LIMIT_MS) return;
  diagnostic('Vela stopped talking to this runtime; exiting rather than lingering');
  for (const controller of activeRuns.values()) controller.abort();
  process.exit(0);
}, Math.min(15_000, Math.max(250, Math.floor(SILENCE_LIMIT_MS / 3))));
watchdog.unref();

const read = createLineReader((message) => {
  lastHeard = Date.now();
  handle(message);
}, (detail) => {
  diagnostic(detail);
  send({ v: PROTOCOL_VERSION, t: 'protocol-error', detail });
});

process.stdin.setEncoding('utf8');
process.stdin.on('data', read);
process.stdin.on('end', () => {
  // Vela closed the pipe: stop cleanly rather than lingering as an orphan.
  for (const controller of activeRuns.values()) controller.abort();
  process.exit(0);
});
process.stdin.on('error', () => process.exit(0));

process.on('uncaughtException', (error) => {
  diagnostic(`uncaught: ${safeMessage(error)}`);
  send({ v: PROTOCOL_VERSION, t: 'fatal', error: safeMessage(error) });
  process.exit(1);
});
process.on('unhandledRejection', (error) => {
  diagnostic(`unhandled rejection: ${safeMessage(error)}`);
});

send({
  v: PROTOCOL_VERSION,
  t: 'ready',
  protocol: PROTOCOL_VERSION,
  node: process.versions.node,
  tramo: packageVersion('@tramo/runtime'),
  spec: packageVersion('@tramo/spec'),
  nodes: supportedNodeIds(),
  maxMessageBytes: MAX_MESSAGE_BYTES,
});
