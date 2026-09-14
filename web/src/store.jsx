import { useResource } from './hooks/useResource.js';
import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { api } from './api.js';
import ReleaseReview from './components/ReleaseReview.jsx';

const AppsContext = createContext(null);

const POLL_INTERVAL = 5000;
let toastSeq = 0;

// Global app state: /api/apps polled every 5s, lifecycle actions with busy
// tracking, and the toast stack. Lives above the router so every page and the
// embedded app view share one source of truth.
export function AppsProvider({ children }) {
  const [platform, setPlatform] = useState(null);
  const [apps, setApps] = useState(null);
  const [error, setError] = useState(null);
  const [busyIds, setBusyIds] = useState(() => new Set());
  const [toasts, setToasts] = useState([]);
  const [releaseSource, reviewRelease] = useState(null);

  const pushToast = useCallback((message, kind = 'error') => {
    const id = ++toastSeq;
    setToasts((prev) => [...prev, { id, message, kind }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 5000);
  }, []);

  const dismissToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const refreshApps = useCallback(async ({ silent = false } = {}) => {
    try {
      const data = await api.getApps();
      setApps(data.apps || []);
      setError(null);
    } catch (err) {
      if (!silent) setError(err);
    }
  }, []);

  useEffect(() => {
    api.getPlatforms().then(setPlatform).catch(() => {});
    refreshApps();
    const timer = setInterval(() => refreshApps({ silent: true }), POLL_INTERVAL);
    return () => clearInterval(timer);
  }, [refreshApps]);

  const runAction = useCallback(
    async (id, action) => {
      if (action === 'install' && apps?.find(app => app.id === id)?.releaseAvailable) {
        reviewRelease({ app_id: id }); return;
      }
      setBusyIds((prev) => new Set(prev).add(id));
      try {
        switch (action) {
          case 'install':
            await api.install(id);
            pushToast('App installed.', 'success');
            break;
          case 'launch':
            await api.launch(id);
            break;
          case 'stop':
            await api.stop(id);
            break;
          case 'uninstall':
            await api.uninstall(id);
            pushToast('App uninstalled.', 'success');
            break;
          default:
            break;
        }
      } catch (err) {
        pushToast(err.message || 'Action failed.');
      } finally {
        setBusyIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
        refreshApps({ silent: true });
      }
    },
    [pushToast, refreshApps, apps],
  );

  const getAppById = useCallback((id) => (apps || []).find((a) => a.id === id) || null, [apps]);

  const value = {
    platform,
    apps,
    error,
    busyIds,
    toasts,
    pushToast,
    dismissToast,
    refreshApps,
    runAction,
    getAppById,
    reviewRelease,
  };

  return <AppsContext.Provider value={value}>{children}{releaseSource && <ReleaseReview source={releaseSource} onClose={() => reviewRelease(null)} onComplete={() => { reviewRelease(null); refreshApps(); pushToast('Release applied. Reopen the app to use it.', 'success'); }} />}</AppsContext.Provider>;
}

export function useApps() {
  const ctx = useContext(AppsContext);
  if (!ctx) throw new Error('useApps must be used inside AppsProvider');
  return ctx;
}

// Engine status for Home/Environments/Settings: refresh after each 10s pause.
export function useEngine() {
  const { data: engine, error: engineError } = useResource(api.getEngine, { intervalMs: 10000 });
  return { engine, engineError };
}

// A changed app ID discards the previous app's status and pending response.
export function useAppStatus(id, { enabled = true } = {}) {
  const load = useCallback(options => api.getStatus(id, options), [id]);
  const { data: status, error } = useResource(load, { enabled: Boolean(id) && enabled, intervalMs: 3000 });
  return { status, statusError: error?.message ?? null };
}
