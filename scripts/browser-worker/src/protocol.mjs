/**
 * Versioned, bounded line protocol between Vela (Python) and the browser worker.
 *
 * Deliberately the same shape as the automation worker's protocol: one JSON
 * object per line on stdout, one per line on stdin, `v` on every message, stderr
 * for human diagnostics only. Two workers that speak the same envelope are two
 * workers a maintainer only has to learn once.
 *
 * What is different is identity. An automation run is one workflow revision; an
 * agent desktop command lands on a particular view, in a particular browser
 * lifetime, under a particular control generation. A command whose identities do
 * not match the session it arrives at is refused rather than applied to whatever
 * page happens to be in front — that is the whole point of carrying them.
 *
 * Images never travel on this channel. Screenshots and downloads are handed over
 * as files with generated names and a digest, so one oversized capture cannot
 * take the control channel down with it.
 */

export const PROTOCOL_VERSION = 1;

/** Largest single message either side will emit or accept, in bytes. */
export const MAX_MESSAGE_BYTES = 1024 * 1024;

/**
 * The identity fields a command carries, from section 2.4 of the plan.
 *
 * `desktopId` and `runtimeSessionId` are required on every command: they say
 * which workspace and which browser lifetime this is for, and a worker restart
 * changes the second one so in-flight commands from before the restart cannot
 * land. The rest are required only where they apply — `viewId` for anything
 * addressing a view, `observationId` for anything whose target came off a
 * screen, `runId`/`actorId` for anything an agent run issued.
 */
export const COMMAND_IDENTITY_FIELDS = Object.freeze([
  'desktopId',
  'runtimeSessionId',
  'commandId',
  'controlEpoch',
  'viewId',
  'runId',
  'actorId',
  'observationId',
]);

/** Message types the host may send. */
export const HOST_MESSAGE_TYPES = Object.freeze(['command', 'effect_result', 'shutdown', 'ping']);

/** Message types the worker may send. */
export const WORKER_MESSAGE_TYPES = Object.freeze([
  'hello',
  'ready',
  'heartbeat',
  'accepted',
  'observation',
  'effect_requested',
  'result',
  'error',
  'closed',
]);

/**
 * Error codes both sides agree on. The host maps these onto its typed API
 * errors; a worker that invents a new string gets reported as `worker_error`
 * rather than silently becoming a new contract.
 */
export const ERROR_CODES = Object.freeze([
  'protocol_error',
  'unsupported_version',
  'unknown_command',
  'identity_mismatch',
  'stale_observation',
  'stale_control_epoch',
  'unknown_view',
  'view_not_ready',
  'navigation_denied',
  'network_denied',
  'command_timeout',
  'capture_unsupported',
  'payload_too_large',
  'runtime_unavailable',
  'worker_error',
]);

export class ProtocolError extends Error {
  constructor(message, code = 'protocol_error') {
    super(message);
    this.code = code;
  }
}

/** Serialize one message, refusing anything past the size bound. */
export function encode(message) {
  const line = JSON.stringify(message);
  if (line === undefined) throw new ProtocolError('message is not JSON-serializable');
  const size = Buffer.byteLength(line, 'utf8');
  if (size > MAX_MESSAGE_BYTES) {
    throw new ProtocolError(
      `message of ${size} bytes exceeds the ${MAX_MESSAGE_BYTES} byte limit`,
      'payload_too_large',
    );
  }
  return line + '\n';
}

/**
 * Split a byte stream into protocol messages.
 *
 * Buffers are bounded: an over-long line is reported once and the reader
 * resynchronises at the next newline rather than growing without limit.
 */
export function createLineReader(onMessage, onProtocolError) {
  let buffer = '';
  let skipping = false;
  return (chunk) => {
    buffer += chunk;
    let index;
    while ((index = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, index);
      buffer = buffer.slice(index + 1);
      if (skipping) {
        skipping = false;
        continue;
      }
      if (!line.trim()) continue;
      let message;
      try {
        message = JSON.parse(line);
      } catch {
        onProtocolError('received a line that is not valid JSON');
        continue;
      }
      if (!message || typeof message !== 'object' || Array.isArray(message)) {
        onProtocolError('received a line that is not a protocol object');
        continue;
      }
      if (message.v !== PROTOCOL_VERSION) {
        onProtocolError(`unsupported protocol version: ${message.v}`, 'unsupported_version');
        continue;
      }
      onMessage(message);
    }
    if (Buffer.byteLength(buffer, 'utf8') > MAX_MESSAGE_BYTES) {
      buffer = '';
      skipping = true;
      onProtocolError(`incoming message exceeded the ${MAX_MESSAGE_BYTES} byte limit`);
    }
  };
}

/**
 * Check a command's identities against the session that would run it.
 *
 * Returns null when the command may proceed, or `{ code, detail }` describing
 * the first mismatch. The caller reports that and does nothing else: guessing
 * which view was meant is how an agent types into the wrong window.
 */
export function checkCommandIdentity(command, session) {
  if (typeof command.commandId !== 'string' || !command.commandId) {
    return { code: 'protocol_error', detail: 'command needs a commandId' };
  }
  if (typeof command.name !== 'string' || !command.name) {
    return { code: 'protocol_error', detail: 'command needs a name' };
  }
  if (command.desktopId !== session.desktopId) {
    return { code: 'identity_mismatch', detail: 'command is for another desktop' };
  }
  if (command.runtimeSessionId !== session.runtimeSessionId) {
    return { code: 'identity_mismatch', detail: 'command is for an earlier runtime session' };
  }
  if (command.controlEpoch !== undefined && command.controlEpoch !== session.controlEpoch) {
    return { code: 'stale_control_epoch', detail: 'control changed since this command was issued' };
  }
  return null;
}
