// Automations use the same authenticated hub client as the rest of the
// dashboard. An app iframe's session cannot reach these routes.
import { ApiError, hubFetch } from './api.js';

async function request(path, options = {}) {
  let response;
  try {
    response = await hubFetch(path, { headers: { Accept: 'application/json' }, ...options });
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError('Cannot reach the Vela backend.', 0);
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body && typeof body.detail === 'string') detail = body.detail;
    } catch {
      // Keep the status-based message when the body is not JSON.
    }
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) return null;
  return response.json();
}

const json = (body) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

const id = (value) => encodeURIComponent(value);

export const automationsApi = {
  list: (options) => request('/api/automations', options),
  catalog: (options) => request('/api/automations/catalog', options),
  status: (options) => request('/api/automations/status', options),
  blueprints: (options) => request('/api/automations/blueprints', options),
  useBlueprint: (blueprintId, name) =>
    request(`/api/automations/blueprints/${id(blueprintId)}`, json({ name: name ?? null })),
  create: (name) => request('/api/automations', json({ name })),
  get: (workflowId, options) => request(`/api/automations/${id(workflowId)}`, options),
  save: (workflowId, body) =>
    request(`/api/automations/${id(workflowId)}`, { ...json(body), method: 'PUT' }),
  rename: (workflowId, name, description) =>
    request(`/api/automations/${id(workflowId)}`, {
      ...json({ name, description }),
      method: 'PATCH',
    }),
  remove: (workflowId) => request(`/api/automations/${id(workflowId)}`, { method: 'DELETE' }),
  duplicate: (workflowId) => request(`/api/automations/${id(workflowId)}/duplicate`, json({})),
  archive: (workflowId) => request(`/api/automations/${id(workflowId)}/archive`, json({})),
  restore: (workflowId) => request(`/api/automations/${id(workflowId)}/restore`, json({})),
  activate: (workflowId) => request(`/api/automations/${id(workflowId)}/activate`, json({})),
  pause: (workflowId) => request(`/api/automations/${id(workflowId)}/pause`, json({})),
  exportOne: (workflowId) => request(`/api/automations/${id(workflowId)}/export`),
  importOne: (payload) => request('/api/automations/import', json(payload)),
  setGrant: (workflowId, body) =>
    request(`/api/automations/${id(workflowId)}/grants`, { ...json(body), method: 'PUT' }),
  rotateWebhook: (workflowId) => request(`/api/automations/${id(workflowId)}/webhook`, json({})),
  startRun: (workflowId, input) =>
    request(`/api/automations/${id(workflowId)}/runs`, json({ input: input ?? null })),
  runs: (params, options) => {
    const query = new URLSearchParams();
    if (params?.workflowId) query.set('workflowId', params.workflowId);
    if (params?.limit) query.set('limit', String(params.limit));
    const suffix = query.toString();
    return request(`/api/automations/runs${suffix ? `?${suffix}` : ''}`, options);
  },
  run: (runId, options) => request(`/api/automations/runs/${id(runId)}`, options),
  runEvents: (runId, after = 0) =>
    request(`/api/automations/runs/${id(runId)}/events?after=${Number(after) || 0}`),
  cancelRun: (runId) => request(`/api/automations/runs/${id(runId)}/cancel`, json({})),
  approvals: (options) => request('/api/automations/approvals', options),
  decide: (runId, gateKey, approved, comment = '') =>
    request(
      `/api/automations/runs/${id(runId)}/approvals/${id(gateKey)}`,
      json({ approved, comment }),
    ),
};

// A run's words and its tone come from `operations/status.js`, which is the
// one status vocabulary for every kind of background work. A workflow's own
// lifecycle — draft, on, paused, archived — is not a run status and stays here.

export const WORKFLOW_STATUS_LABELS = {
  draft: 'Draft',
  active: 'On',
  paused: 'Paused',
  archived: 'Archived',
};

export function triggerLabel(trigger) {
  if (trigger === 'schedule') return 'On a schedule';
  if (trigger === 'webhook') return 'On a web request';
  if (trigger === 'manual') return 'When you run it';
  return 'No trigger yet';
}
