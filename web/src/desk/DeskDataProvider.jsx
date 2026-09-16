// Everything the host widgets read, loaded once for the whole board.
//
// A board can hold four System widgets and three Volume widgets; if each one
// fetched for itself the desk would make seven requests per tick for one
// snapshot. The provider owns the polling, and `tick.js` keeps it off a hidden
// tab, so a desk left open on a second monitor costs nothing while nobody is
// looking at it.
import { createContext, useCallback, useContext, useMemo, useState } from 'react';
import { api } from '../api.js';
import { automationsApi } from '../automationsApi.js';
import { usePolling } from './tick.js';

const DeskDataContext = createContext(null);

// Chosen against what each source actually changes: metrics move constantly,
// a flow run is a human-scale event, a backup is a daily one.
const METRICS_MS = 10000;
const FLOWS_MS = 30000;
const BACKUPS_MS = 60000;
// App summaries change when an app publishes one, which is a human-scale event.
const WIDGETS_MS = 20000;
// The health result only changes when a sweep runs — daily, or because someone
// pressed Run now — so the desk reads it rarely and never starts one itself.
const HEALTH_MS = 120000;

const idle = { data: null, error: null, loaded: false };

function useSource(load, intervalMs) {
  const [state, setState] = useState(idle);
  const run = useCallback(async () => {
    try {
      const data = await load();
      setState({ data, error: null, loaded: true });
    } catch (error) {
      // Keep the last good reading on screen and mark it failed, rather than
      // blanking a widget because one poll did not come back.
      setState((previous) => ({ ...previous, error, loaded: true }));
    }
  }, [load]);
  const refresh = usePolling(run, intervalMs);
  return useMemo(() => ({ ...state, refresh }), [state, refresh]);
}

const loadMetrics = () => api.systemMetrics();
const loadFlows = () => automationsApi.status();
const loadBackups = () => api.getBackups();
const loadAppWidgets = () => api.appWidgets();
const loadHealth = () => api.getDoctor();

export function DeskDataProvider({ children }) {
  const metrics = useSource(loadMetrics, METRICS_MS);
  const flows = useSource(loadFlows, FLOWS_MS);
  const backups = useSource(loadBackups, BACKUPS_MS);
  const appWidgets = useSource(loadAppWidgets, WIDGETS_MS);
  const health = useSource(loadHealth, HEALTH_MS);
  const value = useMemo(
    () => ({ metrics, flows, backups, appWidgets, health }),
    [metrics, flows, backups, appWidgets, health],
  );
  return <DeskDataContext.Provider value={value}>{children}</DeskDataContext.Provider>;
}

/**
 * One source by name. Widgets outside a provider (a fixture, a test) get the
 * idle state rather than throwing, because a widget is not the right place to
 * discover that the page forgot a provider.
 */
export function useDeskData(name) {
  const ctx = useContext(DeskDataContext);
  return ctx?.[name] || { ...idle, refresh: () => {} };
}
