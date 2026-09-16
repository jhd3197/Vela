// Host-only bridge. Never include this module or the bearer token in app content.
export function createBridge({
  frame,
  session,
  context,
  onDirty,
  onNavigate,
  onReady,
  onError,
  fetcher = fetch,
}) {
  const nonce = crypto.randomUUID();
  let connected = false,
    closed = false,
    currentContext = context,
    inFlight = 0;
  const saves = new Map();
  const send = (message) => {
    if (!closed) frame.contentWindow?.postMessage({ ...message, protocol: 1, session: nonce }, '*');
  };
  // '*' is necessary for an opaque destination. All received messages must match
  // the exact source WindowProxy, opaque origin, protocol and per-view nonce.
  const appFetch = async (path, options = {}) => {
    const response = await fetcher(path, {
      ...options,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${session.token}` },
    });
    const result = await response.json();
    if (!response.ok)
      throw Object.assign(new Error(result.detail || 'App request failed'), {
        status: response.status,
      });
    return result;
  };
  const timer = setTimeout(() => {
    if (!connected) onError('The app did not connect. You can retry or return to Apps.');
  }, 10000);
  const listener = async (event) => {
    const message = event.data;
    if (
      closed ||
      event.source !== frame.contentWindow ||
      event.origin !== 'null' ||
      !message ||
      message.protocol !== 1
    )
      return;
    if (message.type === 'vela:ready' && !connected) {
      connected = true;
      clearTimeout(timer);
      send({ type: 'vela:init', context: currentContext });
      onReady();
      return;
    }
    if (!connected || message.session !== nonce) return;
    if (message.type === 'vela:saved') {
      const save = saves.get(message.id);
      if (save) {
        saves.delete(message.id);
        clearTimeout(save.timer);
        message.error ? save.reject(new Error(message.error)) : save.resolve();
      }
      return;
    }
    if (
      message.type !== 'vela:request' ||
      typeof message.id !== 'string' ||
      message.id.length > 100
    )
      return;
    if (inFlight >= 16) {
      send({
        type: 'vela:response',
        id: message.id,
        error: { message: 'Too many pending requests', status: 429 },
      });
      return;
    }
    inFlight++;
    try {
      let result = { ok: true };
      const payload = message.payload;
      if (!payload || typeof payload !== 'object' || Array.isArray(payload))
        throw new Error('Invalid request payload');
      if (message.operation === 'storage.read') {
        if (Object.keys(payload).length)
          throw new Error('Storage reads do not accept an app identity');
        result = await appFetch('/api/app/storage');
      } else if (message.operation === 'storage.write') {
        if (Object.keys(payload).some((key) => !['value', 'revision'].includes(key)))
          throw new Error('Unsupported storage field');
        result = await appFetch('/api/app/storage', {
          method: 'PUT',
          body: JSON.stringify(payload),
        });
      } else if (
        ['storage.snapshots', 'storage.backup', 'storage.export', 'connection.status'].includes(
          message.operation,
        )
      ) {
        if (Object.keys(payload).length) throw new Error('This operation takes no arguments');
        if (message.operation === 'storage.snapshots')
          result = await appFetch('/api/app/storage/snapshots');
        if (message.operation === 'storage.backup')
          result = await appFetch('/api/app/storage/snapshots', { method: 'POST' });
        if (message.operation === 'connection.status')
          result = await appFetch('/api/app/connection');
        if (message.operation === 'storage.export') {
          const saved = await appFetch('/api/app/storage');
          const url = URL.createObjectURL(
            new Blob([JSON.stringify(saved, null, 2)], { type: 'application/json' }),
          );
          const link = document.createElement('a');
          link.href = url;
          link.download = 'vela-app-data.json';
          link.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        }
      } else if (message.operation === 'storage.restore') {
        if (
          Object.keys(payload).some((key) => !['id', 'revision'].includes(key)) ||
          typeof payload.id !== 'string'
        )
          throw new Error('Invalid restore request');
        result = await appFetch(
          `/api/app/storage/snapshots/${encodeURIComponent(payload.id)}/restore`,
          { method: 'POST', body: JSON.stringify({ revision: payload.revision }) },
        );
      } else if (message.operation === 'connection.invoke') {
        if (Object.keys(payload).some((key) => !['operation', 'payload'].includes(key)))
          throw new Error('Invalid connection operation');
        result = await appFetch('/api/app/connection/invoke', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
      } else if (message.operation === 'actions.list') {
        if (Object.keys(payload).length) throw new Error('Action discovery takes no arguments');
        result = await appFetch('/api/app/actions');
      } else if (message.operation === 'actions.invoke') {
        if (
          Object.keys(payload).some((key) => !['app', 'action', 'input', 'key'].includes(key)) ||
          JSON.stringify(payload).length > 40000
        )
          throw new Error('Invalid or oversized action request');
        result = await appFetch('/api/app/actions/invoke', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
      } else if (message.operation === 'widgets.publish') {
        // Declared in the manifest, granted at install, and checked here as
        // well as by the engine: an app that was never granted widgets should
        // not be able to make the host send the request at all.
        if (!(session.capabilities || []).includes('widgets'))
          throw Object.assign(new Error('Widgets capability was not granted'), { status: 403 });
        if (
          Object.keys(payload).some((key) => !['id', 'summary'].includes(key)) ||
          typeof payload.id !== 'string'
        )
          throw new Error('Invalid widget summary');
        if (JSON.stringify(payload.summary ?? {}).length > 4096)
          throw Object.assign(new Error('A summary is at most 4096 bytes'), { status: 413 });
        result = await appFetch(`/api/app/widgets/${encodeURIComponent(payload.id)}`, {
          method: 'PUT',
          body: JSON.stringify({ summary: payload.summary ?? {} }),
        });
      } else if (message.operation === 'navigation.dirty') {
        onDirty({ dirty: payload.dirty === true, canSave: payload.canSave === true });
      } else if (['navigation.return', 'navigation.close'].includes(message.operation)) {
        onNavigate();
      } else {
        throw Object.assign(new Error('Operation is not granted'), { status: 403 });
      }
      send({ type: 'vela:response', id: message.id, result });
    } catch (error) {
      send({
        type: 'vela:response',
        id: message.id,
        error: { message: error.message, status: error.status || 422 },
      });
    } finally {
      inFlight--;
    }
  };
  addEventListener('message', listener);
  return {
    updateContext(next) {
      currentContext = next;
      if (connected) send({ type: 'vela:context', context: next });
    },
    save() {
      const id = crypto.randomUUID();
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          saves.delete(id);
          reject(new Error('The app did not save. You can discard changes and leave.'));
        }, 5000);
        saves.set(id, { resolve, reject, timer });
        send({ type: 'vela:save', id });
      });
    },
    close() {
      if (closed) return;
      closed = true;
      clearTimeout(timer);
      removeEventListener('message', listener);
      saves.forEach(({ timer, reject }) => {
        clearTimeout(timer);
        reject(new Error('App view closed'));
      });
      saves.clear();
      appFetch('/api/app/session', { method: 'DELETE', keepalive: true }).catch(() => {});
    },
  };
}
