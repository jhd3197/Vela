import { createContext, useContext, useMemo } from 'react';
import { api } from './api.js';
import { useResource } from './hooks/useResource.js';

const EngineContext = createContext(null);

// One resource per authenticated dashboard, shared across route transitions.
export function EngineProvider({ children }) {
  const { data, error, loading, refreshing, refresh } = useResource(api.getEngine, {
    intervalMs: 10000,
  });
  const value = useMemo(
    () => ({
      engine: data,
      engineError: error,
      engineLoading: loading,
      engineRefreshing: refreshing,
      refreshEngine: refresh,
    }),
    [data, error, loading, refreshing, refresh],
  );
  return <EngineContext.Provider value={value}>{children}</EngineContext.Provider>;
}

export function useEngine() {
  const value = useContext(EngineContext);
  if (!value) throw new Error('useEngine must be used inside EngineProvider');
  return value;
}
