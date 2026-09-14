// Request lifecycle shared by useResource. Poll again after completion, so a
// slow server never accumulates overlapping requests for this resource.
export function createResource(load, { onChange, intervalMs = 0 }) {
  let disposed = false;
  let inFlight = null;
  let timer;
  const controller = new AbortController();
  let state = { data: null, error: null, loading: true, refreshing: false };
  const publish = (patch) => {
    if (disposed) return;
    state = { ...state, ...patch };
    onChange(state);
  };

  const refresh = () => {
    if (disposed) return Promise.resolve(undefined);
    if (inFlight) return inFlight;
    clearTimeout(timer);
    publish({ refreshing: true });
    inFlight = Promise.resolve().then(() => {
      if (!disposed) return load({ signal: controller.signal });
    }).then(data => {
      publish({ data, error: null });
      return disposed ? undefined : data;
    }).catch(error => {
      publish({ error });
      return undefined;
    }).finally(() => {
      inFlight = null;
      publish({ loading: false, refreshing: false });
      if (!disposed && intervalMs > 0) timer = setTimeout(refresh, intervalMs);
    });
    return inFlight;
  };

  return {
    refresh,
    dispose() {
      disposed = true;
      clearTimeout(timer);
      controller.abort();
    },
  };
}
