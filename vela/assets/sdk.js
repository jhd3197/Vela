/* Plain-script SDK: <script src="_vela/sdk.js"></script>. No framework required. */
(() => {
  const protocol = 1;
  // Optional behaviours this SDK understands, announced in the handshake. A
  // host that does not know a feature simply never uses it, and a host that
  // knows one an older SDK lacks must not use it either — which is why this is
  // negotiated rather than assumed from the protocol number.
  const features = ['approvals'];
  const hostOrigin = new URL(document.currentScript?.src || location.href).origin;
  const pending = new Map();
  const subscribers = new Set();
  const waiting = new Set();
  let session, context, saveHandler, readyResolve, readyReject, approvalHandler;
  const ready = new Promise((resolve, reject) => { readyResolve = resolve; readyReject = reject; });
  // Consumers may attach later; avoid an unhandled rejection on standalone visits.
  ready.catch(() => {});
  const hello = () => parent.postMessage({ type: 'vela:ready', protocol, features }, hostOrigin);
  const retry = setInterval(hello, 250);
  const deadline = setTimeout(() => {
    clearInterval(retry);
    readyReject(new Error('Open this app from Vela to connect.'));
  }, 10000);

  /* How long a request may wait.

     Ten seconds is right for "the host is there and answering". It is wrong for
     "somebody has been asked whether this change may happen", which is a person
     walking back to their computer. So a request that the host says is waiting
     for approval gets a deadline derived from the one Vela set on the question
     itself — never an open-ended wait, and never the app deciding how long it
     may hold a prompt open. */
  const REPLY_MS = 10000;
  const MAX_APPROVAL_WAIT_MS = 21 * 60 * 1000;

  const send = (message) => parent.postMessage({ ...message, protocol, session }, hostOrigin);

  function arm(request, ms, message) {
    clearTimeout(request.timer);
    request.timer = setTimeout(() => {
      pending.delete(request.id);
      waiting.delete(request.id);
      // Tell the host we have stopped waiting, so the question does not sit on
      // somebody's screen with nothing behind it any more.
      if (request.approval) send({ type: 'vela:abandon', id: request.id });
      request.reject(Object.assign(new Error(message), { status: 408 }));
    }, ms);
  }

  async function invoke(operation, payload = {}) {
    await ready;
    const id = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      const request = { id, resolve, reject, timer: null, approval: null };
      arm(request, REPLY_MS, 'Vela did not respond. Try reopening the app.');
      pending.set(id, request);
      send({ type: 'vela:request', id, operation, payload });
    });
  }

  addEventListener('message', async (event) => {
    const message = event.data;
    if (event.source !== parent || event.origin !== hostOrigin || !message || message.protocol !== protocol) return;
    if (message.type === 'vela:init' && !session && typeof message.session === 'string') {
      session = message.session;
      context = message.context;
      clearInterval(retry); clearTimeout(deadline);
      readyResolve(context);
      return;
    }
    if (!session || message.session !== session) return;
    if (message.type === 'vela:response') {
      const request = pending.get(message.id);
      if (!request) return;
      clearTimeout(request.timer); pending.delete(message.id); waiting.delete(message.id);
      if (message.error) request.reject(Object.assign(new Error(message.error.message), { status: message.error.status }));
      else request.resolve(message.result);
    } else if (message.type === 'vela:pending') {
      // Not a result. This request needs a person's answer, and this says so
      // rather than letting the app's own ten seconds run out on a question
      // nobody has seen yet. The host is the one waiting; the app simply stops
      // treating silence as failure.
      const request = pending.get(message.id);
      if (!request || !message.request) return;
      request.approval = message.request;
      waiting.add(message.id);
      const remaining = Number(message.request.expiresAt || 0) * 1000 - Date.now();
      const wait = Math.min(Math.max(Number.isFinite(remaining) ? remaining : 0, REPLY_MS) + 5000, MAX_APPROVAL_WAIT_MS);
      arm(request, wait, 'Nobody answered the request to make this change.');
      if (approvalHandler) {
        try { approvalHandler({ ...message.request, requestId: message.request.requestId }); }
        catch { /* An app's own handler must not break the bridge. */ }
      }
    } else if (message.type === 'vela:context') {
      context = message.context;
      subscribers.forEach((callback) => callback(context));
    } else if (message.type === 'vela:save') {
      try {
        if (!saveHandler) throw new Error('This app does not support saving from the host.');
        await saveHandler();
        send({ type: 'vela:saved', id: message.id });
      } catch (error) {
        send({ type: 'vela:saved', id: message.id, error: error.message });
      }
    }
  });
  window.Vela = Object.freeze({
    ready,
    get context() { return context; },
    onContext(callback) { subscribers.add(callback); return () => subscribers.delete(callback); },
    storage: Object.freeze({
      read: () => invoke('storage.read'), write: (value, revision) => invoke('storage.write', { value, revision }),
      snapshots: () => invoke('storage.snapshots'), backup: () => invoke('storage.backup'),
      restore: (id, revision) => invoke('storage.restore', { id, revision }),
      export: () => invoke('storage.export'),
    }),
    connections: Object.freeze({ status: () => invoke('connection.status'), invoke: (operation, payload = {}) => invoke('connection.invoke', { operation, payload }) }),
    actions: Object.freeze({ list: () => invoke('actions.list'), invoke: (app, action, input, key) => invoke('actions.invoke', { app, action, input, key }) }),
    // Publish one of the widgets this app declared in its manifest. The host
    // renders the summary itself, so this sends data and never markup.
    widgets: Object.freeze({ publish: (id, summary = {}) => invoke('widgets.publish', { id, summary }) }),
    navigation: Object.freeze({ returnToApps: () => invoke('navigation.return'), close: () => invoke('navigation.close') }),
    setUnsavedChanges: (dirty) => invoke('navigation.dirty', { dirty: Boolean(dirty), canSave: Boolean(saveHandler) }),
    onSave(callback) { saveHandler = callback; },
    // Called when a change this app asked for is waiting for the person who
    // owns the server. Optional, and purely so an app can say "waiting for
    // approval" instead of appearing to hang. Nothing here approves anything:
    // the only thing that resolves a request is the owner, in Vela's own
    // controls, on a route no app session can reach.
    onApprovalNeeded(callback) { approvalHandler = callback; },
    get waitingForApproval() { return waiting.size > 0; },
  });
  hello();
})();
