/**
 * Vela browser worker.
 *
 * Vela starts this process, keeps it on a private stdio pipe and stops it. It
 * owns the managed Chromium that agent desktops render in, and nothing else: it
 * holds no Vela credential, opens no port and never reads Vela's data
 * directory. A command arrives, a browser does something, a result goes back.
 *
 * Two lifetimes are watched from both ends. Vela stops the worker when it
 * stops; and if Vela goes quiet — killed, crashed, power cut — the watchdog
 * here exits rather than leaving a browser running that nobody owns.
 *
 * Deliberately not here: any decision about what an agent may do. That is
 * Vela's, in `vela/desktops/`, and a worker that could grant itself anything
 * would make the rest of it decoration.
 */

import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import process from 'node:process';

import { createLineReader, encode, MAX_MESSAGE_BYTES, PROTOCOL_VERSION } from './protocol.mjs';
import { createPolicy } from './network-policy.mjs';
import { browserAvailability, DesktopSession } from './session.mjs';

/** Exit if Vela has not said anything for this long. */
const SILENCE_MS = Number(process.env.VELA_BROWSER_SILENCE_MS || 90_000);

/* ---------------------------------------------------------------- output -- */

let writeFailed = false;

function send(message) {
  if (writeFailed) return;
  try {
    process.stdout.write(encode({ v: PROTOCOL_VERSION, ...message }));
  } catch (error) {
    writeFailed = true;
    diagnostic(`failed to write a protocol message: ${error.message}`);
  }
}

function diagnostic(text) {
  try {
    process.stderr.write(`[vela-browser-worker] ${text}\n`);
  } catch {
    /* A closed stderr must not take the worker down. */
  }
}

/** Errors leaving the worker are messages, never stack traces or file paths. */
function safeMessage(error) {
  const text = error && error.message ? String(error.message) : String(error ?? 'unknown error');
  return text.replace(/\s+/g, ' ').slice(0, 800);
}

/* -------------------------------------------------------------- sessions -- */

/** One `DesktopSession` per agent desktop, by desktop id. */
const sessions = new Map();

function sessionFor(desktopId) {
  const session = sessions.get(desktopId);
  if (!session) {
    const error = new Error('that desktop has no browser session');
    error.code = 'runtime_unavailable';
    throw error;
  }
  return session;
}

/** Where captured frames are handed over. Vela passes the directory it owns. */
let framesDir = null;

/* -------------------------------------------------------------- commands -- */

const COMMANDS = {
  /** Start a browser for one desktop, under the policy Vela decided. */
  async 'session.open'(command) {
    const { desktopId, runtimeSessionId, policy } = command;
    if (sessions.has(desktopId)) {
      // Reopening is not an error — Vela may be recovering — but it must be the
      // same browser rather than a second one nobody is tracking.
      const existing = sessions.get(desktopId);
      return { runtimeSessionId: existing.runtimeSessionId, reused: true };
    }
    const session = new DesktopSession({
      desktopId,
      runtimeSessionId,
      policy: createPolicy(policy || {}),
      onEvent: (event) => send({ type: 'error', code: 'network_denied', desktopId, detail: event }),
    });
    const info = await session.start({ headless: true });
    sessions.set(desktopId, session);
    return { runtimeSessionId, reused: false, ...info };
  },

  async 'session.close'(command) {
    const session = sessions.get(command.desktopId);
    if (!session) return { closed: false };
    sessions.delete(command.desktopId);
    await session.stop();
    return { closed: true };
  },

  async 'view.open'(command) {
    const session = sessionFor(command.desktopId);
    return session.openView(command.viewId, command.url);
  },

  async 'view.close'(command) {
    const session = sessionFor(command.desktopId);
    return { closed: await session.closeView(command.viewId) };
  },

  /**
   * A frame of one view, handed over as a file rather than on this channel.
   *
   * Images do not travel on the control channel: one oversized capture would
   * take the whole protocol down with it. The file is named after its own
   * content so Vela can check it arrived whole.
   */
  async 'view.capture'(command) {
    const session = sessionFor(command.desktopId);
    if (!framesDir) {
      const error = new Error('no frame directory was provided');
      error.code = 'capture_unsupported';
      throw error;
    }
    const frame = await session.captureFrame(command.viewId);
    const digest = createHash('sha256').update(frame.image).digest('hex');
    const name = `${digest}.png`;
    writeFileSync(join(framesDir, name), frame.image);
    return {
      file: name,
      digest,
      bytes: frame.image.length,
      width: frame.width,
      height: frame.height,
      deviceScaleFactor: frame.deviceScaleFactor,
      capturedAt: frame.capturedAt,
      viewId: frame.viewId,
      runtimeSessionId: frame.runtimeSessionId,
      controlEpoch: frame.controlEpoch,
    };
  },

  /** A new control generation. Commands issued under the old one stop working. */
  async 'control.take'(command) {
    const session = sessionFor(command.desktopId);
    return { controlEpoch: session.bumpControlEpoch() };
  },

  async 'runtime.status'() {
    return {
      desktops: [...sessions.keys()],
      views: Object.fromEntries([...sessions].map(([id, s]) => [id, [...s.views.keys()]])),
    };
  },
};

/* --------------------------------------------------------------- dispatch -- */

async function runCommand(message) {
  const handler = COMMANDS[message.name];
  if (!handler) {
    send({
      type: 'error',
      commandId: message.commandId,
      code: 'unknown_command',
      detail: `no command named ${message.name}`,
    });
    return;
  }
  // `accepted` goes out before the work starts, so Vela can tell "still
  // running" from "never arrived" without a timer being the only evidence.
  send({ type: 'accepted', commandId: message.commandId });
  try {
    const result = await handler(message);
    send({
      type: 'result',
      commandId: message.commandId,
      desktopId: message.desktopId,
      runtimeSessionId: message.runtimeSessionId,
      result,
    });
  } catch (error) {
    send({
      type: 'error',
      commandId: message.commandId,
      desktopId: message.desktopId,
      code: error?.code || 'worker_error',
      detail: safeMessage(error),
    });
  }
}

/* ------------------------------------------------------------- lifecycle -- */

let lastHeard = Date.now();
let shuttingDown = false;

async function shutdown(code = 0) {
  if (shuttingDown) return;
  shuttingDown = true;
  const open = [...sessions.values()];
  sessions.clear();
  await Promise.all(open.map((session) => session.stop().catch(() => {})));
  send({ type: 'closed' });
  process.exit(code);
}

const read = createLineReader(
  (message) => {
    lastHeard = Date.now();
    if (message.type === 'ping') {
      send({ type: 'heartbeat', at: Date.now() });
      return;
    }
    if (message.type === 'shutdown') {
      shutdown(0);
      return;
    }
    if (message.type === 'command') {
      runCommand(message);
      return;
    }
    send({ type: 'error', code: 'protocol_error', detail: `unexpected message ${message.type}` });
  },
  (detail, code) => send({ type: 'error', code: code || 'protocol_error', detail }),
);

process.stdin.setEncoding('utf8');
process.stdin.on('data', read);
process.stdin.on('end', () => shutdown(0));
process.on('SIGTERM', () => shutdown(0));
process.on('SIGINT', () => shutdown(0));

// The watchdog. A force-killed Vela cannot send `shutdown`, and a browser left
// running with nobody to stop it is the thing this prevents.
setInterval(() => {
  if (Date.now() - lastHeard > SILENCE_MS) {
    diagnostic('Vela went quiet; closing the browser and exiting');
    shutdown(0);
  }
}, Math.max(1000, Math.floor(SILENCE_MS / 3))).unref();

/* ------------------------------------------------------------------ start -- */

const [, , framesArgument] = process.argv;
if (framesArgument) {
  framesDir = framesArgument;
  if (!existsSync(framesDir)) mkdirSync(framesDir, { recursive: true });
}

const availability = browserAvailability();
send({
  type: 'hello',
  protocol: PROTOCOL_VERSION,
  node: process.versions.node,
  maxMessageBytes: MAX_MESSAGE_BYTES,
  browser: { available: availability.available, reason: availability.reason },
});
send({ type: 'ready', protocol: PROTOCOL_VERSION });
