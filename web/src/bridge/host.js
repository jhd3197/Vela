// Host-only bridge. Never include this module or the bearer token in app content.
import { validateItems } from '../shell/topbar-contract.js';

// The site an http connection belongs to: its host without the first label
// when there are more than two, so api.github.com gives github.com.
export function connectionSite(status) {
  if (status?.provider !== 'http' || typeof status.endpoint !== 'string') return null;
  const labels = new URL(status.endpoint).hostname.split('.');
  return (labels.length > 2 ? labels.slice(1) : labels).join('.');
}

// A surface's path under its app's http connection. The same rule the engine
// applies (`_http_path` in vela/connections.py): one absolute path, no dot
// segments, no encoded separators. Checked here so a request this host will
// not send is refused before it travels.
const SURFACE_PATH = /^\/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{0,1023}$/;

export function surfaceSource(value) {
  if (typeof value !== 'string' || !SURFACE_PATH.test(value) || value.includes('//')) return null;
  if (value.split('/').some((segment) => segment === '.' || segment === '..')) return null;
  const lowered = value.toLowerCase();
  if (['%2e', '%2f', '%5c'].some((code) => lowered.includes(code))) return null;
  return value;
}

export function createBridge({
  frame,
  session,
  context,
  onDirty,
  onNavigate,
  onReady,
  onError,
  // The app this bridge hosts. The host fills it into operations that name an
  // app (surfaces.open), so an app can never speak in another app's name.
  appId = null,
  // What this app has put in the top bar. Unlike every other operation here,
  // it does not travel to the engine: the items belong to this one window, are
  // never stored, and the surface that draws them is in this same page. So the
  // host holds them, and the bridge closing takes them down — see decision D05
  // in `plans/TOP-BAR-PROGRESS.md`.
  onTopBarItems,
  // Ask the page hosting this bridge to open a surface window for
  // `{ appId, source, title }`. Only the dashboard provides one: inside the
  // agent's browser there is no owner session to open windows with, so the
  // operation is refused there rather than pretending.
  onOpenSurface,
  fetcher = fetch,
}) {
  const nonce = crypto.randomUUID();
  let connected = false,
    closed = false,
    currentContext = context,
    inFlight = 0,
    // What the app's SDK said it understands, in the handshake. An older SDK
    // announces nothing, and is never sent anything it cannot read.
    appFeatures = [];
  const saves = new Map();
  // Requests this host is holding open while somebody decides about them.
  const awaiting = new Map();
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
    // 202 is not a result and not a failure: the change needs the owner's
    // answer and nothing has been written. Marked here so the one place that
    // knows how to wait can recognise it.
    if (response.status === 202 && result && result.pending) return { __pending: result.pending };
    return result;
  };

  /* Waiting for a person.

     Vela holds the question; this holds the app's request open while the answer
     is decided, and asks for more time as the deadline approaches. Polling
     rather than a stream because this runs inside an app frame that may be in a
     managed browser with no other channel out, and because a poll that stops
     when the view closes cannot leave anything behind.

     Nothing here approves anything. It reads a state and re-issues the original
     request once the state says the owner said yes; the grant is checked again
     where the effect actually commits. */
  const POLL_MS = 1200;
  const waitForDecision = async (requestId) => {
    while (!closed) {
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      if (closed) return { state: 'cancelled', reason: 'the window was closed' };
      let state;
      try {
        state = await appFetch(`/api/app/approvals/${encodeURIComponent(requestId)}`);
      } catch (error) {
        // A request Vela no longer knows about is one that was cancelled or
        // swept. Reporting that is better than polling a 404 forever.
        return { state: 'cancelled', reason: error.message };
      }
      if (state.state !== 'pending') return state;
      // Ask for more time only as it runs short, and let Vela refuse: the
      // ceiling is Vela's, not this loop's.
      if (Number(state.expiresAt) * 1000 - Date.now() < 45_000) {
        try {
          await appFetch(`/api/app/approvals/${encodeURIComponent(requestId)}/extend`, {
            method: 'POST',
          });
        } catch {
          /* An extension that was refused is not a reason to stop waiting. */
        }
      }
    }
    return { state: 'cancelled', reason: 'the window was closed' };
  };

  /* Run one operation, and see it through an approval if it needs one. */
  const settle = async (messageId, run) => {
    let result = await run();
    // Two rounds at most: asked, answered, retried. A third would mean the
    // grant went away between being issued and being used, and looping on that
    // is how an app ends up asking a person the same question all afternoon.
    for (let round = 0; result && result.__pending && round < 2; round += 1) {
      const request = result.__pending;
      if (!appFeatures.includes('approvals')) {
        // This app's SDK treats silence as failure after ten seconds, so
        // leaving it waiting would be worse than telling it now. The question
        // is withdrawn rather than left on somebody's screen.
        abandon(request.requestId);
        throw Object.assign(
          new Error(
            'This change needs your approval, and this app is built against an ' +
              'SDK that cannot wait for one. Take over the window to make the ' +
              'change yourself, or update the app.',
          ),
          { status: 403 },
        );
      }
      awaiting.set(messageId, request.requestId);
      send({ type: 'vela:pending', id: messageId, request });
      const decided = await waitForDecision(request.requestId);
      awaiting.delete(messageId);
      if (decided.state !== 'approved') {
        throw Object.assign(new Error(explain(decided)), {
          status: decided.state === 'denied' ? 403 : 409,
        });
      }
      result = await run();
    }
    if (result && result.__pending)
      throw Object.assign(new Error('Vela could not complete this change.'), { status: 409 });
    return result;
  };

  const explain = (decided) => {
    if (decided.state === 'denied') return 'You said no to this change.';
    if (decided.state === 'expired') return 'Nobody answered this in time, so nothing changed.';
    return `This change was cancelled: ${decided.reason || 'it is no longer waiting'}.`;
  };

  const abandon = (requestId) => {
    appFetch(`/api/app/approvals/${encodeURIComponent(requestId)}/abandon`, {
      method: 'POST',
      keepalive: true,
    }).catch(() => {});
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
      // What this app's SDK understands. Checked rather than trusted: an app
      // claiming a feature only ever gets *more patience*, never more
      // authority, and anything unrecognised is ignored.
      appFeatures = Array.isArray(message.features)
        ? message.features
            .filter((name) => typeof name === 'string' && name.length < 40)
            .slice(0, 8)
        : [];
      // Tell the engine what this app can do, so a limitation shows before
      // anything walks into it rather than at the moment a change is refused.
      // Best effort: a server that does not record it is not a reason to fail
      // the handshake.
      appFetch('/api/app/features', {
        method: 'POST',
        body: JSON.stringify({ features: appFeatures }),
      }).catch(() => {});
      send({ type: 'vela:init', context: currentContext });
      onReady();
      return;
    }
    if (!connected || message.session !== nonce) return;
    if (message.type === 'vela:abandon') {
      // The app gave up waiting. Withdraw the question rather than leaving it
      // on somebody's screen with nothing behind it.
      const requestId = awaiting.get(message.id);
      if (requestId) {
        awaiting.delete(message.id);
        abandon(requestId);
      }
      return;
    }
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
      // One run of the operation. A thunk, because an operation that needs
      // the owner's approval is run again once it has one — same request,
      // same values, same digest, now with a grant behind it.
      const perform = async () => {
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
        } else if (message.operation === 'topbar.publish') {
          // Declared in the manifest and granted at install, exactly like
          // widgets. An app that was never granted the top bar should not be
          // able to make the host draw anything there at all.
          if (!(session.capabilities || []).includes('topbar'))
            throw Object.assign(new Error('Top bar capability was not granted'), { status: 403 });
          if (Object.keys(payload).some((key) => key !== 'items'))
            throw new Error('Invalid top bar request');
          // Checked before it is kept, not while it is drawn: an app that sent
          // something this host will not show should be told so.
          onTopBarItems?.(validateItems(payload.items ?? []));
          result = { ok: true, items: (payload.items ?? []).length };
        } else if (message.operation === 'surfaces.open') {
          // Declared in the manifest and granted at install, exactly like the
          // top bar. The document itself is fetched by the hub through the
          // app's connection; this only asks for the window.
          if (!(session.capabilities || []).includes('surfaces'))
            throw Object.assign(new Error('Surfaces capability was not granted'), { status: 403 });
          if (Object.keys(payload).some((key) => !['source', 'title'].includes(key)))
            throw new Error('Invalid surface request');
          if (payload.title !== undefined && typeof payload.title !== 'string')
            throw new Error('Invalid surface request');
          const source = surfaceSource(payload.source);
          if (!source) throw new Error('A surface source is an absolute path on the app’s connection');
          if (!onOpenSurface || !appId)
            throw Object.assign(new Error('This window cannot open surfaces'), { status: 409 });
          // The calling app's id comes from the session this bridge was built
          // for, never from the payload: an app cannot open a window in
          // another app's name.
          result =
            (await onOpenSurface({
              appId,
              source,
              title: (payload.title || '').slice(0, 120),
            })) || { ok: true };
        } else if (message.operation === 'navigation.dirty') {
          onDirty({ dirty: payload.dirty === true, canSave: payload.canSave === true });
        } else if (message.operation === 'navigation.open') {
          if (
            Object.keys(payload).some((key) => key !== 'url') ||
            typeof payload.url !== 'string' ||
            payload.url.length > 2048
          )
            throw new Error('Invalid link');
          // A sandboxed app cannot open windows itself. It may ask for a link
          // on the site of the service it is connected to (api.github.com
          // allows github.com and its subdomains), and nowhere else.
          const status = await appFetch('/api/app/connection');
          const site = connectionSite(status);
          const link = new URL(payload.url);
          if (
            !site ||
            link.protocol !== 'https:' ||
            link.username ||
            link.password ||
            !(link.hostname === site || link.hostname.endsWith(`.${site}`))
          )
            throw Object.assign(
              new Error('Apps can only open links on the site they are connected to'),
              { status: 403 },
            );
          window.open(link.href, '_blank', 'noopener,noreferrer');
          result = { ok: true };
        } else if (['navigation.return', 'navigation.close'].includes(message.operation)) {
          onNavigate();
        } else {
          throw Object.assign(new Error('Operation is not granted'), { status: 403 });
        }
        return result;
      };
      const result = await settle(message.id, perform);
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
      // Whatever this app put in the top bar goes with its window. Not a
      // courtesy: an item left behind would name an app that is no longer
      // there and raise a window that no longer exists.
      onTopBarItems?.([]);
      // A question asked by a window that is closing has nobody left to answer
      // for. Withdraw each one; the engine cancels them too when the view
      // record closes, and neither side relies on the other remembering.
      awaiting.forEach((requestId) => abandon(requestId));
      awaiting.clear();
      saves.forEach(({ timer, reject }) => {
        clearTimeout(timer);
        reject(new Error('App view closed'));
      });
      saves.clear();
      appFetch('/api/app/session', { method: 'DELETE', keepalive: true }).catch(() => {});
    },
  };
}
