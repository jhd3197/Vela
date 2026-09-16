import { useResource } from './hooks/useResource.js';
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, isProcessApp } from './api.js';
import ReleaseReview from './components/ReleaseReview.jsx';
export { useEngine } from './engine.jsx';

const AppsContext = createContext(null);

const POLL_INTERVAL = 5000;
let toastSeq = 0;

// Global app state: /api/apps polled every 5s, lifecycle actions with busy
// tracking, and the toast stack. Lives above the router so every page and the
// embedded app view share one source of truth.
export function AppsProvider({ children }) {
  const navigate = useNavigate();
  const [platform, setPlatform] = useState(null);
  const [openingId, setOpeningId] = useState(null);
  const opening = useRef(false);
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
    api
      .getPlatforms()
      .then(setPlatform)
      .catch(() => {});
    refreshApps();
    const timer = setInterval(() => refreshApps({ silent: true }), POLL_INTERVAL);
    return () => clearInterval(timer);
  }, [refreshApps]);

  const runAction = useCallback(
    async (id, action) => {
      if (action === 'install' && apps?.find((app) => app.id === id)?.releaseAvailable) {
        reviewRelease({ app_id: id });
        return;
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

  // The one deliberate way to open an app. Nothing else may start a process:
  // not polling, not a prefetch, not a render effect, and not a passive visit
  // to /app/<id>. An installed, supported process app that is stopped is
  // started once through the existing launch action, and only then opened.
  // Repeated clicks collapse into the first one.
  const openApp = useCallback(
    async (id, { returnTo = '/' } = {}) => {
      if (opening.current) return;
      const app = (apps || []).find((item) => item.id === id);
      const go = () => navigate(`/app/${id}`, { state: { returnTo } });
      const needsStart =
        app?.installed && app.supported && isProcessApp(app) && !app.running && !busyIds.has(id);
      if (!needsStart) {
        go();
        return;
      }
      opening.current = true;
      setOpeningId(id);
      try {
        await api.launch(id);
        await refreshApps();
        go();
      } catch (err) {
        pushToast(err.message || `Could not open ${app.name}.`);
      } finally {
        opening.current = false;
        setOpeningId(null);
      }
    },
    [apps, busyIds, navigate, refreshApps, pushToast],
  );

  const getAppById = useCallback((id) => (apps || []).find((a) => a.id === id) || null, [apps]);

  // Which apps the user pinned to the rail, in their chosen order. Ids are core
  // ids or installed-app ids; the rail drops any that no longer resolve. Kept
  // here so the rail and the Launchpad share one source of truth and a pin made
  // in one shows in the other at once.
  const [pinned, setPinned] = useState([]);
  useEffect(() => {
    api
      .getSettings()
      .then((data) => {
        if (Array.isArray(data?.rail?.pinned)) setPinned(data.rail.pinned);
      })
      .catch(() => {});
  }, []);

  const persistPins = useCallback(
    (next) => {
      api
        .updateSettings({ rail: { pinned: next } })
        .catch((err) => pushToast(err.message || 'Could not save your pinned apps.'));
    },
    [pushToast],
  );

  const pinApp = useCallback(
    (id) =>
      setPinned((prev) => {
        if (prev.includes(id)) return prev;
        const next = [...prev, id];
        persistPins(next);
        return next;
      }),
    [persistPins],
  );

  const unpinApp = useCallback(
    (id) =>
      setPinned((prev) => {
        if (!prev.includes(id)) return prev;
        const next = prev.filter((entry) => entry !== id);
        persistPins(next);
        return next;
      }),
    [persistPins],
  );

  const movePin = useCallback(
    (id, dir) =>
      setPinned((prev) => {
        const from = prev.indexOf(id);
        const to = from + (dir === 'up' ? -1 : 1);
        if (from < 0 || to < 0 || to >= prev.length) return prev;
        const next = prev.slice();
        [next[from], next[to]] = [next[to], next[from]];
        persistPins(next);
        return next;
      }),
    [persistPins],
  );

  const value = {
    platform,
    apps,
    error,
    busyIds,
    openingId,
    openApp,
    toasts,
    pushToast,
    dismissToast,
    refreshApps,
    runAction,
    getAppById,
    reviewRelease,
    pinned,
    pinApp,
    unpinApp,
    movePin,
  };

  return (
    <AppsContext.Provider value={value}>
      {children}
      {releaseSource && (
        <ReleaseReview
          source={releaseSource}
          onClose={() => reviewRelease(null)}
          onComplete={() => {
            reviewRelease(null);
            refreshApps();
            pushToast('Release applied. Reopen the app to use it.', 'success');
          }}
        />
      )}
    </AppsContext.Provider>
  );
}

export function useApps() {
  const ctx = useContext(AppsContext);
  if (!ctx) throw new Error('useApps must be used inside AppsProvider');
  return ctx;
}

// A changed app ID discards the previous app's status and pending response.
export function useAppStatus(id, { enabled = true } = {}) {
  const load = useCallback((options) => api.getStatus(id, options), [id]);
  const { data: status, error } = useResource(load, {
    enabled: Boolean(id) && enabled,
    intervalMs: 3000,
  });
  return { status, statusError: error?.message ?? null };
}
