// The desktop routes, in one place.
//
// Separate from `api.js` because a desktop is scoped state: every call here
// names which workspace it is for, and a caller that forgets is a caller that
// would have written to whichever desktop happened to be first.
import { ApiError, hubFetch } from '../api.js';

async function request(path, options = {}) {
  let response;
  try {
    response = await hubFetch(path, { headers: { Accept: 'application/json' }, ...options });
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError('Cannot reach the Vela backend.', 0);
  }
  if (!response.ok) {
    if (response.status === 423) dispatchEvent(new Event('vela:locked'));
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body && typeof body.detail === 'string') detail = body.detail;
    } catch {
      // Non-JSON error body; keep the status-based message.
    }
    const error = new ApiError(detail, response.status);
    // A 409 carries the revision to reload from, the same header the desk has
    // always sent. Without it the caller can only guess and overwrite.
    const revision = response.headers.get('X-Vela-Desk-Revision');
    if (revision !== null) error.revision = Number(revision);
    throw error;
  }
  return response.status === 204 ? null : response.json();
}

const json = (method, body) => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

const scope = (id) => `/api/desktops/${encodeURIComponent(id)}`;

export const desktopsApi = {
  list: (options) => request('/api/desktops', options),
  get: (id, options) => request(scope(id), options),
  create: (name) => request('/api/desktops', json('POST', name ? { name } : {})),
  rename: (id, name, revision) => request(scope(id), json('PATCH', { name, revision })),
  remove: (id) => request(scope(id), { method: 'DELETE' }),

  boards: (id, options) => request(`${scope(id)}/boards`, options),
  saveBoards: (id, revision, boards) =>
    request(`${scope(id)}/boards`, json('PUT', { revision, boards })),

  appearance: (id, options) => request(`${scope(id)}/appearance`, options),
  saveAppearance: (id, patch) => request(`${scope(id)}/appearance`, json('PUT', patch)),

  views: (id, options) => request(`${scope(id)}/views`, options),
  openView: (id, body) => request(`${scope(id)}/views`, json('POST', body)),
  updateView: (id, viewId, patch) =>
    request(`${scope(id)}/views/${encodeURIComponent(viewId)}`, json('PATCH', patch)),
  closeView: (id, viewId) =>
    request(`${scope(id)}/views/${encodeURIComponent(viewId)}`, { method: 'DELETE' }),
  // Selecting is not saving an arrangement, which is why it has its own route
  // and carries no revision: clicking a window must not conflict with a drag
  // somebody else is finishing.
  selectView: (id, viewId) => request(`${scope(id)}/selected-view`, json('POST', { viewId })),

  layout: (id, options) => request(`${scope(id)}/layout`, options),
  saveLayout: (id, revision, patch) =>
    request(`${scope(id)}/layout`, json('PUT', { revision, ...patch })),

  // The image goes up as raw bytes with its type in the header: one picture
  // does not justify a multipart parser on the server.
  putWallpaper: (id, file) =>
    request(`${scope(id)}/wallpaper`, {
      method: 'PUT',
      headers: { 'Content-Type': file.type },
      body: file,
    }),
  deleteWallpaper: (id) => request(`${scope(id)}/wallpaper`, { method: 'DELETE' }),

  // ---- agent desktops
  //
  // Every one of these names its desktop. An agent surface that acted on
  // "the current desktop" would eventually act on the wrong one.
  policy: (id, options) => request(`${scope(id)}/policy`, options),
  savePolicy: (id, body) => request(`${scope(id)}/policy`, json('PUT', body)),
  enableAgent: (id) => request(`${scope(id)}/enable-agent`, { method: 'POST' }),
  disableAgent: (id) => request(`${scope(id)}/disable-agent`, { method: 'POST' }),
  runtime: (options) => request('/api/desktops/runtime', options),
  models: (options) => request('/api/desktops/models', options),
  attention: (options) => request('/api/desktops/attention', options),

  tasks: (id, options) => request(`${scope(id)}/tasks`, options),
  submitTask: (id, body) => request(`${scope(id)}/tasks`, json('POST', body)),
  task: (id, runId, options) => request(`${scope(id)}/tasks/${encodeURIComponent(runId)}`, options),
  controlTask: (id, runId, action) =>
    request(`${scope(id)}/tasks/${encodeURIComponent(runId)}/control`, json('POST', { action })),
  // Asking again is a new task, never the old one carrying on: what it did, it
  // did, and there is no state to resume into.
  retryTask: (id, runId) =>
    request(`${scope(id)}/tasks/${encodeURIComponent(runId)}/retry`, { method: 'POST' }),
  resumeQueue: (id) => request(`${scope(id)}/queue/resume`, { method: 'POST' }),
  // `after` is the cursor a viewer already has. Reconnecting with it is what
  // makes a dropped connection cost nothing.
  events: (id, after = 0, options) =>
    request(`${scope(id)}/events?after=${Number(after) || 0}`, options),

  // ---- watching and control
  viewer: (id, options) => request(`${scope(id)}/viewer`, options),
  frame: (id, viewId, maxAgeMs = 400) =>
    request(`${scope(id)}/views/${encodeURIComponent(viewId)}/frame?maxAgeMs=${maxAgeMs}`),
  // The bytes, fetched rather than pointed at. An <img src> cannot carry the
  // hub bearer, and putting a credential in a URL to work around that is how a
  // token ends up in a log. The caller gets a blob and owns revoking it.
  frameBytes: async (id, viewId, digest) => {
    const response = await hubFetch(
      `${scope(id)}/views/${encodeURIComponent(viewId)}/frame/${encodeURIComponent(digest)}`,
      { headers: { Accept: 'image/png' }, cache: 'no-store' },
    );
    if (!response.ok) throw new ApiError('That picture is no longer available.', response.status);
    return URL.createObjectURL(await response.blob());
  },
  takeOver: (id, viewId) => request(`${scope(id)}/takeover`, json('POST', { viewId })),
  sendInput: (id, leaseId, body) =>
    request(`${scope(id)}/takeover/${encodeURIComponent(leaseId)}/input`, json('POST', body)),
  releaseControl: (id, leaseId) =>
    request(`${scope(id)}/takeover/${encodeURIComponent(leaseId)}`, { method: 'DELETE' }),

  // ---- files and website sessions
  //
  // The file picker lives in the dashboard. An agent is told which files exist
  // and can attach one by id; it never opens a picker and never names a path.
  files: (id, options) => request(`${scope(id)}/files`, options),
  addFile: (id, file) =>
    request(`${scope(id)}/files`, {
      method: 'POST',
      headers: {
        'Content-Type': file.type || 'application/octet-stream',
        // The name travels in a header because it is a label, not a path: the
        // bytes are stored under a name Vela generates either way.
        'X-Vela-Filename': encodeURIComponent(file.name || 'file').replace(/%20/g, ' '),
      },
      body: file,
    }),
  removeFile: (id, fileId) =>
    request(`${scope(id)}/files/${encodeURIComponent(fileId)}`, { method: 'DELETE' }),
  fileUrl: (id, fileId) => `${scope(id)}/files/${encodeURIComponent(fileId)}`,
  clearUnresolved: (id, digest) =>
    request(`${scope(id)}/files?digest=${encodeURIComponent(digest)}`, { method: 'DELETE' }),

  websiteSession: (id, options) => request(`${scope(id)}/session`, options),
  keepWebsiteSession: (id) => request(`${scope(id)}/session`, { method: 'POST' }),
  eraseWebsiteSession: (id) => request(`${scope(id)}/session`, { method: 'DELETE' }),

  approvals: (id, options) => request(`${scope(id)}/approvals`, options),
  resolveApproval: (id, requestId, body) =>
    request(`${scope(id)}/approvals/${encodeURIComponent(requestId)}`, json('POST', body)),
  wallpaperUrl: (id) => `${scope(id)}/wallpaper`,
};
