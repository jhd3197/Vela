// The one way an app is opened, from anywhere.
//
// The rail, All apps, a desk widget, the search field and a keyboard shortcut
// all mean the same thing by "open Finance", and they all used to reach it by
// navigating to `/app/finance`, which replaced whatever was on screen. They now
// go through here instead, which puts the app in a window on the desk and keeps
// the page fallback for the cases where a window is not the right answer.
//
// Three behaviours, in this order, because each one is what a person means:
//
// - **Already open?** Bring that window forward and select it, the way a
//   taskbar button does. Nobody clicking an app's icon twice wants two copies
//   of it, and a second window would be a second session of the same app.
// - **Minimized?** Restore it, in its own remembered place. The app kept
//   running while it was away and whatever was typed into it is still there.
// - **Otherwise** start the app if it has a process and is stopped, open a
//   window for it and go to the desk, because the desk is where windows are.
//
// Nothing here decides *whether* a window is right; `open-app.js` does, with no
// React in it. This is the part that has to talk to the server and the router.
import { useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { api, isProcessApp } from '../api.js';
import { PHONE } from '../breakpoints.js';
import useMediaQuery from '../hooks/useMediaQuery.js';
import { useApps } from '../store.jsx';
import { useAppsOverlay } from './apps-overlay-context.js';
import { useDesktops } from './DesktopsProvider.jsx';
import { WINDOW, presentationFor, windowFor } from './open-app.js';

/**
 * `open(id, { returnTo })` — the same shape the page-based opener had, so a
 * call site changes by one line and keeps meaning what it meant.
 */
export default function useOpenApp() {
  const { apps, openApp, refreshApps, pushToast } = useApps();
  const { views, selectedId } = useDesktops();
  const { appsOpen, closeApps } = useAppsOverlay();
  const navigate = useNavigate();
  const location = useLocation();
  const narrow = useMediaQuery(PHONE);

  return useCallback(
    async (id, { returnTo = location.pathname } = {}) => {
      const app = (apps || []).find((item) => item.id === id) || null;
      const { presentation } = presentationFor(app, { narrow, desktopId: selectedId });
      // The page is not a failure mode. It is the right presentation for a
      // phone, for an app that opens outside Vela, and for one that is not
      // installed yet — and it is the path that knows how to say so.
      if (presentation !== WINDOW) {
        openApp(id, { returnTo });
        return null;
      }

      // The desk is where windows are drawn, so opening one means going there.
      // Both happen before the round trip, so the window is never opened behind
      // a page that is about to be replaced anyway — and closing the grid comes
      // first because, when it was reached by its own link, closing it is
      // itself a navigation, and the desk has to be the one that wins.
      if (appsOpen) closeApps();
      if (location.pathname !== '/') navigate('/');

      const existing = windowFor(views.views, id);
      if (existing) {
        // Put away is brought back, with the motion that goes with it; already
        // showing is brought forward. Neither opens a second window, and
        // neither touches the session running inside the first one.
        if (existing.window?.minimized) views.toggleWindow(existing.id);
        else views.patchView(existing.id, { raise: true });
        views.select(existing.id);
        return existing;
      }

      try {
        // Starting the app and opening a window are two different things: a
        // window with nothing running in it is not an answer, and a running app
        // with no window is not what was asked for.
        if (isProcessApp(app) && !app.running) {
          await api.launch(id);
          await refreshApps();
        }
        const view = await views.open({ kind: 'app', appId: id, title: app.name });
        if (view?.id) views.select(view.id);
        // Counted here for the same reason the page opener counts it: this is
        // the deliberate-open path, and it is the only one worth counting for
        // All apps' Frequent tab. Fire-and-forget — a count that does not save
        // is not a reason to fail to open the app somebody asked for.
        api.recordUsage(id).catch(() => {});
        return view;
      } catch (error) {
        pushToast(error.message || `Could not open ${app?.name || 'that app'}.`, 'error');
        return null;
      }
    },
    [
      apps,
      appsOpen,
      closeApps,
      location.pathname,
      narrow,
      navigate,
      openApp,
      pushToast,
      refreshApps,
      selectedId,
      views,
    ],
  );
}
