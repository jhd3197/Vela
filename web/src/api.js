// Centralized Vela API client. All calls use relative /api paths so the
// Vite dev proxy (dev) and the FastAPI backend (prod) both resolve them.

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

let hubSession;
export function acceptHubSession(token) {
  hubSession = token ? Promise.resolve(token) : null;
}

// A 401 usually means this browser's hub token went stale, so one retry with a
// fresh token is right. It is wrong for a route that checks a credential: there
// a 401 is the answer, and retrying would spend two of the five attempts the
// engine allows. Those callers pass `retryUnauthorized: false`.
export async function hubFetch(path, { retryUnauthorized = true, ...options } = {}) {
  const getToken = () => {
    if (!hubSession) {
      hubSession = fetch('/api/session', {
        headers: { 'X-Vela-Bootstrap': '1' },
        cache: 'no-store',
      })
        .then(async (response) => {
          if (!response.ok) {
            if (response.status === 401) dispatchEvent(new Event('vela:auth-required'));
            throw new ApiError('Sign in to Vela to continue.', response.status);
          }
          return (await response.json()).token;
        })
        .catch((error) => {
          hubSession = null;
          throw error;
        });
    }
    return hubSession;
  };
  const send = async () => {
    const headers = new Headers(options.headers);
    headers.set('Authorization', `Bearer ${await getToken()}`);
    return fetch(path, { ...options, headers });
  };
  let response = await send();
  if (response.status === 401 && retryUnauthorized) {
    hubSession = null;
    response = await send();
  }
  return response;
}

async function request(path, options = {}) {
  let res;
  try {
    res = await hubFetch(path, {
      headers: { Accept: 'application/json' },
      ...options,
    });
  } catch (error) {
    if (error instanceof ApiError) throw error;
    // Network-level failure: backend unreachable, DNS, etc.
    throw new ApiError('Cannot reach the Vela backend.', 0);
  }

  if (!res.ok) {
    // 423 is the engine saying this session is locked. Every consumer learns
    // it from one event rather than each one re-checking for itself.
    if (res.status === 423) dispatchEvent(new Event('vela:locked'));
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === 'string') detail = body.detail;
    } catch {
      // Non-JSON error body; keep the status-based message.
    }
    throw new ApiError(detail, res.status);
  }

  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  getPhoneAccess: () => request('/api/phone-access'),
  enablePhoneAccess: (value) =>
    request('/api/phone-access', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(value),
    }),
  disablePhoneAccess: () => request('/api/phone-access', { method: 'DELETE' }),
  addWebApp: (value) =>
    request('/api/web-apps', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(value),
    }),
  updateWebApp: (id, value) =>
    request(`/api/web-apps/${encodeURIComponent(id)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(value),
    }),
  removeWebApp: (id, revision) =>
    request(`/api/web-apps/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ revision }),
    }),
  appActions: (id) => request(`/api/apps/${encodeURIComponent(id)}/actions`),
  actionHistory: (id) => request(`/api/apps/${encodeURIComponent(id)}/actions/history`),
  grantAction: (id, app, action, allow, sourceContract, targetContract) =>
    request(`/api/apps/${encodeURIComponent(id)}/actions/grant`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ app, action, allow, sourceContract, targetContract }),
    }),
  catalog: (options) => request('/api/catalog', options),
  refreshCatalog: () => request('/api/catalog/refresh', { method: 'POST' }),
  prepareRelease: (source) =>
    source.file
      ? request('/api/releases/upload', {
          method: 'POST',
          headers: { 'Content-Type': 'application/zip' },
          body: source.file,
        })
      : request('/api/releases/prepare', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(source),
        }),
  commitRelease: (review) =>
    request(`/api/releases/${review.review}/commit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ capabilities: review.capabilities, operations: review.operations }),
    }),
  cancelRelease: (review) => request(`/api/releases/${review}`, { method: 'DELETE' }),
  releaseHistory: (id, options) => request(`/api/apps/${encodeURIComponent(id)}/releases`, options),
  upgrade: (id) => request(`/api/apps/${encodeURIComponent(id)}/upgrade`, { method: 'POST' }),
  previewMigration: (id, value) =>
    request(`/api/apps/${encodeURIComponent(id)}/migration/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value, revision: 0 }),
    }),
  migrate: (id, value, revision) =>
    request(`/api/apps/${encodeURIComponent(id)}/migration`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value, revision }),
    }),
  getConnection: (id, options) =>
    request(`/api/apps/${encodeURIComponent(id)}/connection`, options),
  bindConnection: (id, endpoint) =>
    request(`/api/apps/${encodeURIComponent(id)}/connection`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ endpoint }),
    }),
  disconnectConnection: (id) =>
    request(`/api/apps/${encodeURIComponent(id)}/connection`, { method: 'DELETE' }),
  openSession: (id) => request(`/api/apps/${encodeURIComponent(id)}/session`, { method: 'POST' }),
  health: () => request('/api/health'),
  getPlatforms: () => request('/api/platforms'),
  getEngine: (options) => request('/api/engine', options),
  getApps: () => request('/api/apps'),
  getApp: (id) => request(`/api/apps/${encodeURIComponent(id)}`),
  getStatus: (id, options) => request(`/api/apps/${encodeURIComponent(id)}/status`, options),
  install: (id) => request(`/api/apps/${encodeURIComponent(id)}/install`, { method: 'POST' }),
  uninstall: (id) => request(`/api/apps/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  launch: (id) => request(`/api/apps/${encodeURIComponent(id)}/launch`, { method: 'POST' }),
  stop: (id) => request(`/api/apps/${encodeURIComponent(id)}/stop`, { method: 'POST' }),
  iconUrl: (id) => `/api/apps/${encodeURIComponent(id)}/icon`,

  // App lock. The secret is sent once per attempt and never stored anywhere in
  // the browser; the engine owns enrollment, lock state and throttling.
  getSecurity: (options) => request('/api/security', options),
  enrollSecurity: (value) =>
    request('/api/security/enroll', {
      method: 'POST',
      retryUnauthorized: false,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(value),
    }),
  setSecurityTimeout: (value) =>
    request('/api/security', {
      method: 'PATCH',
      retryUnauthorized: false,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(value),
    }),
  disableSecurity: (password) =>
    request('/api/security', {
      method: 'DELETE',
      retryUnauthorized: false,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    }),
  lockNow: () => request('/api/security/lock', { method: 'POST' }),
  unlock: (value) =>
    request('/api/security/unlock', {
      method: 'POST',
      retryUnauthorized: false,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(value),
    }),
  // The only request that defers the inactivity lock. Polling deliberately
  // does not, so a phone left on a table still locks on time.
  reportActivity: () =>
    request('/api/security/activity', { method: 'POST', headers: { 'X-Vela-Activity': '1' } }),

  getSettings: () => request('/api/settings'),
  updateSettings: (patch) =>
    request('/api/settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }),
  getAiStatus: () => request('/api/ai/status'),
  testNotify: () => request('/api/notify/test', { method: 'POST' }),
  getNotifications: (options) => request('/api/notifications', options),
  systemMetrics: (options) => request('/api/system/metrics', options),
  appWidgets: (options) => request('/api/widgets', options),
  // The image goes up as raw bytes with its type in the header: one picture
  // does not justify a multipart parser on the server.
  putWallpaper: (file) =>
    request('/api/wallpaper', {
      method: 'PUT',
      headers: { 'Content-Type': file.type },
      body: file,
    }),
  deleteWallpaper: () => request('/api/wallpaper', { method: 'DELETE' }),
  // Health checks. Reading is cheap and never starts a sweep; `runDoctor` is
  // the deliberate action behind "Run now".
  getDoctor: (options) => request('/api/doctor', options),
  runDoctor: () => request('/api/doctor/run', { method: 'POST' }),
  repairDoctor: (key) =>
    request(`/api/doctor/${encodeURIComponent(key)}/repair`, { method: 'POST' }),

  // Logs. `pattern` switches the read into a search; a `/…/` pattern is a
  // regular expression, anything else a case-insensitive substring.
  getLogs: (options) => request('/api/logs', options),
  readLog: (name, { lines = 200, fromEnd = true, pattern = '' } = {}, options) => {
    const query = new URLSearchParams({ lines: String(lines), from_end: String(fromEnd) });
    if (pattern) query.set('pattern', pattern);
    return request(`/api/logs/${encodeURIComponent(name)}?${query}`, options);
  },
  clearLog: (name) =>
    request(`/api/logs/${encodeURIComponent(name)}`, {
      method: 'DELETE',
      headers: { 'X-Vela-Confirm': 'clear' },
    }),
  // The download carries the hub token like every other call, so it cannot be
  // a bare link: fetch the bytes and hand the browser a blob to save.
  downloadLog: async (name) => {
    const response = await hubFetch(`/api/logs/${encodeURIComponent(name)}/download`);
    if (!response.ok) throw new ApiError(`Could not download ${name}.`, response.status);
    return response.blob();
  },

  getBackups: (options) => request('/api/backups', options),
  createBackup: () => request('/api/backups', { method: 'POST' }),
  verifyBackup: (name) =>
    request(`/api/backups/${encodeURIComponent(name)}/verify`, { method: 'POST' }),
};

const PLATFORM_LABELS = {
  posix: 'macOS / Linux',
  windows: 'Windows',
  android: 'Android',
  web: 'Web (PWA)',
};

export function platformLabel(key) {
  return PLATFORM_LABELS[key] || key || 'Unknown';
}

// The backend reports the active runtime on every app summary/status:
// 'web' when the manifest has a web entry (web wins), 'process' when the app
// runs as a child process, null when unsupported on this platform.
export function isWebApp(app) {
  return app?.runtime === 'web';
}

export function isProcessApp(app) {
  return app?.runtime === 'process';
}

export function formatBytes(bytes) {
  if (bytes == null || Number.isNaN(bytes)) return '—';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unit = 'B';
  for (const next of units) {
    if (value < 1024) break;
    value /= 1024;
    unit = next;
  }
  return `${value >= 100 ? Math.round(value) : value.toFixed(1)} ${unit}`;
}

// Compact relative time ("just now", "5m ago", "2d ago") for timestamps
// produced by the hub (ISO strings in local time).
export function relTime(ts) {
  const then = new Date(ts);
  if (Number.isNaN(then.getTime())) return '—';
  const seconds = Math.max(0, Math.floor((Date.now() - then.getTime()) / 1000));
  if (seconds < 45) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return then.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}
