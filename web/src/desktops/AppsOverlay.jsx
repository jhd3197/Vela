// All Apps, over whatever you were doing.
//
// It used to be a route, which meant opening it unmounted the page underneath.
// That was fine while a page was only ever a desk; it stops being fine the
// moment a page holds an app with something unsaved in it. Looking for an app
// is not leaving what you were doing, so the overlay opens above the current
// page and closing it puts focus back on whatever asked for it.
//
// The same shape as `SettingsProvider`, deliberately: `/apps` still works as a
// link and a bookmark, and opens the overlay over the desk.
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import Launchpad from '../pages/Launchpad.jsx';
import { launchpadReturnTo } from '../shortcuts.js';

const AppsOverlayContext = createContext(null);

const ROUTE = '/apps';

/** `{ appsOpen, openApps, closeApps, toggleApps }` — no-ops outside a provider. */
export const useAppsOverlay = () =>
  useContext(AppsOverlayContext) || {
    appsOpen: false,
    openApps: () => {},
    closeApps: () => {},
    toggleApps: () => {},
  };

export default function AppsOverlayProvider({ children }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [request, setRequest] = useState(null);
  // Where the person was looking before, so closing returns them there rather
  // than to a focus ring on the document.
  const opener = useRef(null);

  // Two ways in: the `/apps` link, and asking for it in place. A bookmarked
  // URL keeps working, and neither way is a different feature.
  const routed = location.pathname === ROUTE;
  const open = routed || request === location.key;

  const openApps = useCallback(() => {
    opener.current = document.activeElement;
    setRequest(location.key);
  }, [location.key]);

  const closeApps = useCallback(() => {
    setRequest(null);
    if (routed) {
      // Arrived by link: go where the person was before, replacing the entry so
      // Back does not step straight into the overlay again.
      navigate(launchpadReturnTo(), { replace: true });
      return;
    }
    const returnTo = opener.current;
    opener.current = null;
    if (returnTo?.isConnected && typeof returnTo.focus === 'function') {
      returnTo.focus({ preventScroll: true });
    }
  }, [routed, navigate]);

  const toggleApps = useCallback(() => {
    if (open) closeApps();
    else openApps();
  }, [open, closeApps, openApps]);

  // Arriving at `/apps` by link means the page behind is whatever the route
  // renders; leaving the route closes the overlay with it.
  useEffect(() => {
    if (!routed && request && request !== location.key) setRequest(null);
  }, [routed, request, location.key]);

  // The page behind is scenery while the grid is up: its own search field must
  // not be the second searchbox a screen reader finds, and Tab must not walk
  // into a form nobody can see. The rail stays live — it is how you get out.
  useEffect(() => {
    if (!open) return undefined;
    const workspace = document.querySelector('.shell > .workspace');
    if (!workspace) return undefined;
    // `inert` takes it out of the tab order and out of hit-testing;
    // `aria-hidden` takes it out of what a screen reader reads. Both, because
    // support for the first is newer than the dashboards people run.
    workspace.inert = true;
    workspace.setAttribute('aria-hidden', 'true');
    return () => {
      workspace.inert = false;
      workspace.removeAttribute('aria-hidden');
    };
  }, [open]);

  return (
    <AppsOverlayContext.Provider value={{ appsOpen: open, openApps, closeApps, toggleApps }}>
      {children}
      {open && (
        <div className="apps-overlay">
          <Launchpad onClose={closeApps} />
        </div>
      )}
    </AppsOverlayContext.Provider>
  );
}
