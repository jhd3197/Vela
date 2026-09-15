// Bots and rooms API client.
//
// Bot profiles are configuration rather than transcripts, so unlike the
// conversation endpoints these keep working while chat history is off.

import { hubFetch } from './api.js';

async function readError(res) {
  let detail = `Request failed (${res.status})`;
  try {
    const body = await res.json();
    if (body && typeof body.detail === 'string') detail = body.detail;
    else if (Array.isArray(body?.detail)) {
      const parts = body.detail.map((d) => d?.msg).filter(Boolean);
      if (parts.length) detail = `${detail}: ${parts.join('; ')}`;
    }
  } catch {
    // Non-JSON error body; keep the status-based message.
  }
  const error = new Error(detail);
  error.status = res.status;
  return error;
}

async function json(path, options = {}) {
  const res = await hubFetch(path, {
    headers: {
      Accept: 'application/json',
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    },
    ...options,
  });
  if (!res.ok) throw await readError(res);
  return res.status === 204 ? null : res.json();
}

export const listBots = ({ archived = false, signal } = {}) =>
  json(`/api/bots${archived ? '?archived=true' : ''}`, { signal });

export const readBot = (id, { signal } = {}) =>
  json(`/api/bots/${encodeURIComponent(id)}`, { signal });

export const createBot = (bot) => json('/api/bots', { method: 'POST', body: JSON.stringify(bot) });

export const updateBot = (id, patch) =>
  json(`/api/bots/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(patch) });

export const duplicateBot = (id) =>
  json(`/api/bots/${encodeURIComponent(id)}/duplicate`, { method: 'POST' });

export const deleteBot = (id) => json(`/api/bots/${encodeURIComponent(id)}`, { method: 'DELETE' });

/**
 * Ask the model to draft instructions from a plain description.
 * Never throws for a model problem: it resolves with `{ok: false, error}` so the
 * editor can say what happened and let the person write them by hand.
 */
export async function draftInstructions(purpose) {
  try {
    return await json('/api/bots/draft', { method: 'POST', body: JSON.stringify({ purpose }) });
  } catch (failure) {
    return { ok: false, instructions: '', error: failure?.message || 'Could not draft.' };
  }
}

export const setRoomMembers = (id, botIds, leadBotId) =>
  json(`/api/chat/conversations/${encodeURIComponent(id)}/members`, {
    method: 'PUT',
    body: JSON.stringify({ botIds, leadBotId: leadBotId || '' }),
  });

export const readRun = (id, { signal } = {}) =>
  json(`/api/chat/conversations/${encodeURIComponent(id)}/run`, { signal });

export const stopRun = (id) =>
  json(`/api/chat/conversations/${encodeURIComponent(id)}/run`, { method: 'DELETE' });
