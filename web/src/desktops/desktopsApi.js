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
  wallpaperUrl: (id) => `${scope(id)}/wallpaper`,
};
