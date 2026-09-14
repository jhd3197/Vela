import { useCallback, useEffect, useRef, useState } from 'react';
import { createResource } from './resource.js';

// Pass a stable loader (an API method or useCallback). A changed loader starts
// a new resource; responses from the old one cannot replace its state.
export function useResource(load, { enabled = true, intervalMs = 0 } = {}) {
  const current = useRef(null);
  const [snapshot, setSnapshot] = useState(null);

  useEffect(() => {
    if (!enabled) {
      setSnapshot(null);
      return undefined;
    }
    const resource = createResource(load, {
      intervalMs,
      onChange: state => setSnapshot({ load, intervalMs, ...state }),
    });
    current.current = resource;
    resource.refresh();
    return () => {
      resource.dispose();
      current.current = null;
    };
  }, [load, enabled, intervalMs]);

  const refresh = useCallback(() => current.current?.refresh() ?? Promise.resolve(undefined), []);
  const state = enabled && snapshot?.load === load && snapshot.intervalMs === intervalMs
    ? snapshot : { data: null, error: null, loading: enabled, refreshing: false };
  return { data: state.data, error: state.error, loading: state.loading,
    refreshing: state.refreshing, refresh };
}
