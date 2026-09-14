// Assistant API client. The chat endpoint streams SSE over POST, so it uses
// a raw fetch with a ReadableStream reader rather than the JSON wrapper in
// api.js. Frames arrive as `data: {json}` separated by blank lines, with
// `: keepalive` comment lines mixed in.

import { hubFetch } from './api.js';

async function readError(res) {
  let detail = `Request failed (${res.status})`;
  try {
    const body = await res.json();
    if (body && typeof body.detail === 'string') detail = body.detail;
    else if (body && typeof body.error === 'string') detail = body.error;
  } catch {
    // Non-JSON error body; keep the status-based message.
  }
  return new Error(detail);
}

export async function getAiStatus() {
  const res = await hubFetch('/api/ai/status', { headers: { Accept: 'application/json' } });
  if (!res.ok) throw await readError(res);
  return res.json();
}

export async function getSettings() {
  const res = await hubFetch('/api/settings', { headers: { Accept: 'application/json' } });
  if (!res.ok) throw await readError(res);
  return res.json();
}

export async function sendToPhone({ title, message }) {
  let res;
  try {
    res = await hubFetch('/api/notify/publish', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, message }),
    });
  } catch {
    throw new Error('Cannot reach the Vela backend.');
  }
  if (!res.ok) throw await readError(res);
  return res.status === 204 ? null : res.json().catch(() => null);
}

/**
 * POST /api/chat and consume the event stream.
 * Calls onEvent(parsedJson) for each `data:` frame; resolves when the stream
 * ends. Aborts cleanly through the provided AbortSignal.
 */
export async function streamChat({ message, conversationId, signal, onEvent }) {
  let res;
  try {
    res = await hubFetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      signal,
      body: JSON.stringify(conversationId ? { message, conversationId } : { message }),
    });
  } catch (err) {
    if (err?.name === 'AbortError') throw err;
    throw new Error('Cannot reach the Vela backend.', { cause: err });
  }
  if (!res.ok || !res.body) throw await readError(res);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const consume = (part) => {
    const line = part.trim();
    if (!line.startsWith('data:')) return; // keepalives and comments
    try {
      onEvent(JSON.parse(line.slice(5)));
    } catch {
      // Malformed frame; skip it rather than kill the stream.
    }
  };

  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split('\n\n');
      buffer = parts.pop() || '';
      parts.forEach(consume);
    }
    if (buffer.trim()) consume(buffer);
  } finally {
    await reader.cancel().catch(() => {});
  }
}
