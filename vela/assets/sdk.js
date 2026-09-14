/* Plain-script SDK: <script src="_vela/sdk.js"></script>. No framework required. */
(() => {
  const protocol = 1;
  const hostOrigin = new URL(document.currentScript?.src || location.href).origin;
  const pending = new Map();
  const subscribers = new Set();
  let session, context, saveHandler, readyResolve, readyReject;
  const ready = new Promise((resolve, reject) => { readyResolve = resolve; readyReject = reject; });
  // Consumers may attach later; avoid an unhandled rejection on standalone visits.
  ready.catch(() => {});
  const hello = () => parent.postMessage({ type: 'vela:ready', protocol }, hostOrigin);
  const retry = setInterval(hello, 250);
  const deadline = setTimeout(() => {
    clearInterval(retry);
    readyReject(new Error('Open this app from Vela to connect.'));
  }, 10000);

  const send = (message) => parent.postMessage({ ...message, protocol, session }, hostOrigin);
  async function invoke(operation, payload = {}) {
    await ready;
    const id = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { pending.delete(id); reject(new Error('Vela did not respond. Try reopening the app.')); }, 10000);
      pending.set(id, { resolve, reject, timer });
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
      clearTimeout(request.timer); pending.delete(message.id);
      if (message.error) request.reject(Object.assign(new Error(message.error.message), { status: message.error.status }));
      else request.resolve(message.result);
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
    navigation: Object.freeze({ returnToApps: () => invoke('navigation.return'), close: () => invoke('navigation.close') }),
    setUnsavedChanges: (dirty) => invoke('navigation.dirty', { dirty: Boolean(dirty), canSave: Boolean(saveHandler) }),
    onSave(callback) { saveHandler = callback; },
  });
  hello();
})();
