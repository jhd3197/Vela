import { useCallback, useMemo } from 'react';
import { api } from '../api.js';
import { automationsApi } from '../automationsApi.js';
import { desktopsApi } from '../desktops/desktopsApi.js';
import { useResource } from '../hooks/useResource.js';
import { useApps } from '../store.jsx';
import { useDesktops } from '../desktops/DesktopsProvider.jsx';
import { normalizeOperations } from './normalize.js';

// Every source is read through the loaders the dashboard already has — this
// adds no polling convention of its own, and two surfaces asking at the same
// moment share one request now that `hubFetch` coalesces.
const RUN_INTERVAL = 15000;
const SLOW_INTERVAL = 120000;

/**
 * Everything the engine is doing for the user, in one list.
 *
 *   active         — queued, running or waiting
 *   needsAttention — waiting on a person, or failed
 *   recent         — the rest, newest first
 */
export function useOperations({ enabled = true, limit = 8 } = {}) {
  const { apps, busyIds } = useApps();
  const { desktops } = useDesktops();

  const loadRuns = useCallback((options) => automationsApi.runs({ limit }, options), [limit]);
  const loadAttention = useCallback((options) => desktopsApi.attention(options), []);
  const loadUpdates = useCallback((options) => api.getUpdates(options), []);
  const loadUpdateJob = useCallback((options) => api.updateJob(options), []);
  const loadBackups = useCallback((options) => api.getBackups(options), []);
  const loadDoctor = useCallback((options) => api.getDoctor(options), []);

  const runs = useResource(loadRuns, { enabled, intervalMs: RUN_INTERVAL });
  const attention = useResource(loadAttention, { enabled, intervalMs: RUN_INTERVAL });
  const updates = useResource(loadUpdates, { enabled, intervalMs: SLOW_INTERVAL });
  const updateJob = useResource(loadUpdateJob, { enabled, intervalMs: RUN_INTERVAL });
  const backups = useResource(loadBackups, { enabled, intervalMs: SLOW_INTERVAL });
  const doctor = useResource(loadDoctor, { enabled, intervalMs: SLOW_INTERVAL });

  // An app is being installed when it is busy and not installed yet. The
  // engine has no endpoint for an install in progress, so this is the only
  // record there is; see the progress file.
  const installing = useMemo(
    () => (apps || []).filter((app) => busyIds?.has(app.id) && !app.installed),
    [apps, busyIds],
  );

  const operations = useMemo(
    () =>
      normalizeOperations({
        runs: runs.data,
        attention: attention.data,
        desktops,
        updates: updates.data,
        updateJob: updateJob.data,
        backups: backups.data,
        doctor: doctor.data,
        installing,
      }),
    [
      runs.data,
      attention.data,
      desktops,
      updates.data,
      updateJob.data,
      backups.data,
      doctor.data,
      installing,
    ],
  );

  return useMemo(() => {
    const active = operations.filter((item) =>
      ['queued', 'running', 'waiting'].includes(item.status),
    );
    return {
      operations,
      active,
      needsAttention: operations.filter((item) => item.needsAttention),
      recent: operations.filter((item) => !active.includes(item)),
      // Loaded once every source has answered or failed; a partial list is
      // still a list, so nothing waits for all six.
      loading: [runs, attention, updates, updateJob, backups, doctor].every((one) => one.loading),
    };
  }, [operations, runs, attention, updates, updateJob, backups, doctor]);
}

export default useOperations;
