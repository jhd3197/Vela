/**
 * Versioned, bounded line protocol between Vela (Python) and this worker.
 *
 * One JSON object per line on stdout; one per line on stdin. stderr carries
 * human diagnostics only and is never parsed. Every message carries `v` (the
 * protocol version) so a mismatched pair refuses to talk instead of guessing.
 *
 * The worker never opens a socket, never receives a Vela credential and never
 * performs an app or notification effect itself: it asks the host, which
 * re-authorizes every request. A separate process is isolation for crashes and
 * lifetimes, not a security sandbox.
 */

export const PROTOCOL_VERSION = 1;

/** Largest single message either side will emit or accept, in bytes. */
export const MAX_MESSAGE_BYTES = 1024 * 1024;

export class ProtocolError extends Error {}

/** Serialize one message, refusing anything past the size bound. */
export function encode(message) {
  const line = JSON.stringify(message);
  if (line === undefined) throw new ProtocolError('message is not JSON-serializable');
  const size = Buffer.byteLength(line, 'utf8');
  if (size > MAX_MESSAGE_BYTES) {
    throw new ProtocolError(`message of ${size} bytes exceeds the ${MAX_MESSAGE_BYTES} byte limit`);
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
      if (!message || typeof message !== 'object' || message.v !== PROTOCOL_VERSION) {
        onProtocolError(`unsupported protocol version: ${message && message.v}`);
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
