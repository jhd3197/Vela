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

function hubToken() {
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
}

async function sendWithToken(path, options) {
  const headers = new Headers(options.headers);
  headers.set('Authorization', `Bearer ${await hubToken()}`);
  return fetch(path, { ...options, headers });
}

// A 401 usually means this browser's hub token went stale, so one retry with a
// fresh token is right. It is wrong for a route that checks a credential: there
// a 401 is the answer, and retrying would spend two of the five attempts the
// engine allows. Those callers pass `retryUnauthorized: false`.
async function sendWithRetry(path, options, retryUnauthorized) {
  let response = await sendWithToken(path, options);
  if (response.status === 401 && retryUnauthorized) {
    hubSession = null;
    response = await sendWithToken(path, options);
  }
  return response;
}

// In-flight GET coalescing. Origin: the `isCoalescable` block in ServerKit
// `frontend/src/services/api/client.js` (MIT, same owner).
//
// Two callers asking for the same path at the same moment share one network
// request. This is NOT a cache: the entry is dropped the moment the request
// settles, so nobody ever reads a stale body. It only collapses the overlap,
// which is where the duplicates actually come from — React's StrictMode
// double-invokes every effect in development, and two independent components
// own the same endpoint (the rail and the desk status bar both read
// `/api/app-widgets`, the rail and Launchpad both read `/api/settings`).
const inFlightGets = new Map();

// The only request header the dashboard's reads carry. It says how to encode
// the answer, not which answer to give, so two callers that both send it are
// asking the same question. Anything else — `X-Vela-Confirm`, a bootstrap
// header, a range — can change what comes back and is never shared.
const SHARED_HEADERS = new Map([['accept', 'application/json']]);

function isCoalescable(options) {
  if ((options.method || 'GET').toUpperCase() !== 'GET') return false;
  if (options.body != null) return false;
  for (const [name, value] of new Headers(options.headers || {})) {
    if (SHARED_HEADERS.get(name) !== value) return false;
  }
  return true;
}

function abortError(signal) {
  return signal.reason ?? new DOMException('The operation was aborted.', 'AbortError');
}

// The caller's own signal settles the caller's promise and nothing else. It
// must not cancel a request somebody else is still waiting for — and it must
// still cancel one nobody is, which is why the join counts its waiters.
function untilAborted(promise, signal) {
  if (!signal) return promise;
  if (signal.aborted) return Promise.reject(abortError(signal));
  return new Promise((resolve, reject) => {
    const onAbort = () => reject(abortError(signal));
    signal.addEventListener('abort', onAbort, { once: true });
    promise.then(resolve, reject).finally(() => signal.removeEventListener('abort', onAbort));
  });
}

// 204 and its relatives are defined to have no body; constructing a `Response`
// that gives them one throws.
const EMPTY_STATUS = new Set([101, 103, 204, 205, 304]);

// Read the body once, here, and give every caller a response of its own built
// from those bytes. Cloning the response instead would tee its stream, which
// makes even the single-caller case wait for a second reader that never comes.
// Reading it once costs nothing extra: every caller was going to read it.
async function readOnce(response) {
  const body = EMPTY_STATUS.has(response.status) ? null : await response.arrayBuffer();
  return () =>
    new Response(body, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
}

async function joinInFlightGet(path, start, signal) {
  if (signal?.aborted) throw abortError(signal);
  let entry = inFlightGets.get(path);
  if (!entry) {
    entry = { controller: new AbortController(), waiting: 0, settled: false };
    entry.promise = start(entry.controller.signal)
      .then(readOnce)
      .finally(() => {
        entry.settled = true;
        if (inFlightGets.get(path) === entry) inFlightGets.delete(path);
      });
    // A request every caller walked away from still rejects when it is
    // abandoned. That rejection has no owner left, and an unowned rejection is
    // a crash in Node and a console error in the browser.
    entry.promise.catch(() => {});
    inFlightGets.set(path, entry);
  }
  entry.waiting += 1;
  try {
    // Every caller gets a response of its own, so one of them consuming the
    // body cannot empty it for the other.
    return (await untilAborted(entry.promise, signal))();
  } finally {
    entry.waiting -= 1;
    if (entry.waiting === 0 && !entry.settled) {
      // Everyone who joined has given up. Drop the entry before aborting, so a
      // caller arriving now starts a fresh request rather than joining a dying
      // one.
      if (inFlightGets.get(path) === entry) inFlightGets.delete(path);
      entry.controller.abort();
    }
  }
}

export function hubFetch(path, { retryUnauthorized = true, ...options } = {}) {
  // A route that turned the retry off is checking a credential; its answer is
  // about the credential it was given, so it is never shared.
  if (!retryUnauthorized || !isCoalescable(options)) {
    return sendWithRetry(path, options, retryUnauthorized);
  }
  const { signal, ...shared } = options;
  // The retry runs inside the shared promise, so a token that went stale is
  // refreshed once and both callers get the retried body.
  return joinInFlightGet(
    path,
    (sharedSignal) => sendWithRetry(path, { ...shared, signal: sharedSignal }, true),
    signal,
  );
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
  // Managed web apps: an existing self-hosted server Vela installs and runs.
  // `openManaged` returns a one-use link into the app's own web address; it is
  // never a token to keep, and it expires in thirty seconds.
  managedApps: (options) => request('/api/managed', options),
  managedApp: (id, options) => request(`/api/managed/${encodeURIComponent(id)}`, options),
  managedStatus: (id, options) => request(`/api/managed/${encodeURIComponent(id)}/status`, options),
  reviewManaged: (source) =>
    source.file
      ? request('/api/managed/review/upload', {
          method: 'POST',
          headers: { 'Content-Type': 'application/zip' },
          body: source.file,
        })
      : request('/api/managed/review', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ folder: source.folder }),
        }),
  installManaged: (review, { startWithVela = false } = {}) =>
    request(`/api/managed/review/${review.review}/install`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        artifactDigest: review.artifactDigest,
        packageDigest: review.packageDigest,
        trust: 'trusted-native',
        startWithVela,
      }),
    }),
  cancelManagedReview: (review) => request(`/api/managed/review/${review}`, { method: 'DELETE' }),
  openManaged: (id, { path, start = true } = {}) =>
    request(`/api/managed/${encodeURIComponent(id)}/launch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: path ?? null, start }),
    }),
  startManaged: (id) => request(`/api/managed/${encodeURIComponent(id)}/start`, { method: 'POST' }),
  stopManaged: (id) => request(`/api/managed/${encodeURIComponent(id)}/stop`, { method: 'POST' }),
  setManagedStartup: (id, startWithVela) =>
    request(`/api/managed/${encodeURIComponent(id)}/startup`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ startWithVela }),
    }),
  backUpManaged: (id, note = '') =>
    request(`/api/managed/${encodeURIComponent(id)}/snapshots`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note }),
    }),
  restoreManaged: (id, snapshot) =>
    request(
      `/api/managed/${encodeURIComponent(id)}/snapshots/${encodeURIComponent(snapshot)}/restore`,
      { method: 'POST' },
    ),
  rollbackManaged: (id, release) =>
    request(
      `/api/managed/${encodeURIComponent(id)}/releases/${encodeURIComponent(release)}/rollback`,
      { method: 'POST' },
    ),
  eraseManagedData: (id) =>
    request(`/api/managed/${encodeURIComponent(id)}/data/erase`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: id }),
    }),
  removeManaged: (id, { eraseData = false } = {}) =>
    request(`/api/managed/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ eraseData }),
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

  // Themes. Vela never fetches one: `importTheme` sends the contents of a file
  // the user picked, which the browser read locally.
  getThemes: (options) => request('/api/themes', options),
  getTheme: (slug, options) => request(`/api/themes/${encodeURIComponent(slug)}`, options),
  importTheme: (document, { replace = false } = {}) =>
    request('/api/themes/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(replace ? { theme: document, replace: true } : document),
    }),
  removeTheme: (slug) => request(`/api/themes/${encodeURIComponent(slug)}`, { method: 'DELETE' }),
  themeExportUrl: (slug) => `/api/themes/${encodeURIComponent(slug)}/export`,
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
  // Later: stop showing one app's attention flag on the desk for a while. The
  // summary is untouched and the app is told nothing.
  snoozeWidget: (appId, widgetId) =>
    request(`/api/widgets/${encodeURIComponent(appId)}/${encodeURIComponent(widgetId)}/snooze`, {
      method: 'POST',
    }),
  // Open counts behind the Launchpad's Frequent tab. Counted and kept on this
  // computer; recording one is fire-and-forget, so a failure never blocks the
  // app the user asked for.
  // Files: every call names a share by id and a path relative to it. There is
  // deliberately no call that takes a whole path.
  fileShares: (options) => request('/api/files', options),
  listFiles: (share, path = '', options) =>
    request(`/api/files/${encodeURIComponent(share)}?path=${encodeURIComponent(path)}`, options),
  // A file's bytes, fetched with the hub token and handed back as a blob URL.
  // The engine authenticates by header only, so an `<img src>` or a plain link
  // pointing at `/api/...` would be refused; the caller revokes the URL when it
  // is finished with it.
  fileBlobUrl: async (share, path, { inline = false } = {}) => {
    const response = await hubFetch(
      `/api/files/${encodeURIComponent(share)}/download?path=${encodeURIComponent(path)}${
        inline ? '&inline=true' : ''
      }`,
    );
    if (!response.ok) {
      let detail = `Request failed (${response.status})`;
      try {
        const body = await response.json();
        if (body && typeof body.detail === 'string') detail = body.detail;
      } catch {
        // Non-JSON error body; keep the status-based message.
      }
      throw new ApiError(detail, response.status);
    }
    return URL.createObjectURL(await response.blob());
  },
  createFolder: (share, path, name) =>
    request(`/api/files/${encodeURIComponent(share)}/folder`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, name }),
    }),
  renameFile: (share, path, name) =>
    request(`/api/files/${encodeURIComponent(share)}/rename`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, name }),
    }),
  deleteFile: (share, path) =>
    request(`/api/files/${encodeURIComponent(share)}?path=${encodeURIComponent(path)}`, {
      method: 'DELETE',
    }),
  uploadFile: (share, path, file) =>
    request(
      `/api/files/${encodeURIComponent(share)}/upload?path=${encodeURIComponent(
        path,
      )}&name=${encodeURIComponent(file.name)}`,
      { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file },
    ),

  usage: (options) => request('/api/usage', options),
  // The desk's one outbound request, and only while the user has it on. The
  // server makes it, caches it and answers with nothing at all when it is off.
  weather: (options) => request('/api/weather', options),
  locateWeather: (place) =>
    request('/api/weather/locate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ place }),
    }),
  recordUsage: (id) => request(`/api/usage/${encodeURIComponent(id)}`, { method: 'POST' }),
  // The image goes up as raw bytes with its type in the header: one picture
  // does not justify a multipart parser on the server.
  putWallpaper: (file) =>
    request('/api/wallpaper', {
      method: 'PUT',
      headers: { 'Content-Type': file.type },
      body: file,
    }),
  deleteWallpaper: () => request('/api/wallpaper', { method: 'DELETE' }),
  // Errors the engine recorded, and the dashboard's own reports.
  getErrors: (query, options) =>
    request(`/api/errors?${new URLSearchParams(query || {})}`, options),
  errorStats: (options) => request('/api/errors/stats', options),
  reportClientError: (value) =>
    request('/api/errors/client', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(value),
    }),
  resolveError: (id, resolved = true) =>
    request(`/api/errors/${encodeURIComponent(id)}/resolve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ resolved }),
    }),
  deleteError: (id) => request(`/api/errors/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  // Support bundles. Built on this computer; sharing one is the user's move.
  getSupportBundles: (options) => request('/api/support-bundle', options),
  createSupportBundle: () => request('/api/support-bundle', { method: 'POST' }),
  downloadSupportBundle: async (name) => {
    const response = await hubFetch(`/api/support-bundle/${encodeURIComponent(name)}`);
    if (!response.ok) throw new ApiError(`Could not download ${name}.`, response.status);
    return response.blob();
  },

  // Updates. Reading is local; checking is the one request Vela makes on its
  // own behalf, and only while the preference is on.
  getUpdates: (options) => request('/api/updates', options),
  checkUpdates: () => request('/api/updates/check', { method: 'POST' }),
  updateJob: (options) => request('/api/updates/job', options),
  updateReport: (options) => request('/api/updates/report', options),
  // Replacing Vela with another copy of Vela carries its own header, like
  // every other action that cannot be undone with one click.
  applyUpdate: () =>
    request('/api/updates/apply', {
      method: 'POST',
      headers: { 'X-Vela-Confirm': 'update' },
    }),
  rollbackUpdate: () =>
    request('/api/updates/rollback', {
      method: 'POST',
      headers: { 'X-Vela-Confirm': 'rollback' },
    }),

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
  backupStats: (options) => request('/api/backups/stats', options),
  // Restoring replaces live files, so it carries the deliberate header the
  // engine requires.
  restoreBackup: (name) =>
    request(`/api/backups/${encodeURIComponent(name)}/restore`, {
      method: 'POST',
      headers: { 'X-Vela-Confirm': 'restore' },
    }),
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

// A managed web app: an application Vela installed and runs as a local service,
// published on a web address of its own. It is neither a packaged SDK app nor a
// saved connection to a site someone else runs, and the dashboard has to keep
// the three apart wherever it offers an action.
export function isManagedApp(app) {
  return app?.runtime === 'managed-service';
}

const MANAGED_STATES = {
  stopped: { label: 'Stopped', tone: 'idle' },
  starting: { label: 'Starting', tone: 'busy' },
  ready: { label: 'Running', tone: 'good' },
  failed: { label: 'Failed', tone: 'bad' },
};

const MANAGED_OPERATIONS = {
  install: 'Installing',
  update: 'Updating',
  backup: 'Backing up',
  restore: 'Restoring',
  rollback: 'Going back',
  remove: 'Removing',
  erase: 'Erasing data',
};

// What to show for a managed app, in one place, so the window, the row and the
// drawer cannot disagree about whether an app is running. An operation in
// progress wins over the service state: "Updating" is more useful than
// "Stopped" when the reason it stopped is the update.
export function managedState(app) {
  const managed = app?.managed;
  if (!managed) return { label: 'Unknown', tone: 'idle', detail: '' };
  if (managed.operation) {
    return {
      label: MANAGED_OPERATIONS[managed.operation] || 'Working',
      tone: 'busy',
      detail: managed.detail || '',
      busy: true,
    };
  }
  const state = MANAGED_STATES[managed.state] || MANAGED_STATES.stopped;
  return { ...state, detail: managed.detail || '' };
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
