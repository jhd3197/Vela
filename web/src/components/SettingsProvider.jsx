import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import Settings from '../pages/Settings.jsx';
import { PHONE } from '../breakpoints.js';
import { useDesktops } from '../desktops/DesktopsProvider.jsx';
import useMediaQuery from '../hooks/useMediaQuery.js';
import { useApps } from '../store.jsx';

const SettingsContext = createContext(null);

export const useSettingsPopup = () => useContext(SettingsContext);

/** The Settings window on this desktop, if there is one. */
export const settingsWindowIn = (views) =>
  (views?.views || []).find((view) => view.kind === 'host' && view.surface === 'settings') || null;

// Settings is one place with two presentations, the way a desktop OS and a
// phone OS each have their own. On a wide screen it is a window on the desk —
// moved, snapped, minimized and closed like any other, named in the top bar
// and the rail — and there is only ever one of it: asking again brings it
// forward at the section asked for. On a phone it is a screen over the page,
// which keeps the page underneath mounted, including an unsent chat.
// Bookmarked `/settings#section` URLs open whichever the screen calls for.
export default function SettingsProvider({ children }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { views } = useDesktops();
  const { pushToast } = useApps();
  // The width at which Settings stops being a window and becomes a screen.
  const compact = useMediaQuery(PHONE);
  const [request, setRequest] = useState(null);
  // Which section the window was last asked for. `n` counts requests so the
  // same section asked for twice is still two requests.
  const [sectionRequest, setSectionRequest] = useState({ id: null, n: 0 });
  const routed = location.pathname === '/settings';

  const openWindow = useCallback(
    async (section, { replace = false } = {}) => {
      setSectionRequest((previous) => ({ id: section || null, n: previous.n + 1 }));
      // Windows are drawn on the desk, so opening one means going there.
      if (location.pathname !== '/') navigate('/', { replace });
      const existing = settingsWindowIn(views);
      if (existing) {
        if (existing.window?.minimized) views.toggleWindow(existing.id);
        else views.patchView(existing.id, { raise: true });
        views.select(existing.id);
        return;
      }
      try {
        const view = await views.open({ kind: 'host', surface: 'settings', title: 'Settings' });
        if (view?.id) views.select(view.id);
      } catch (error) {
        pushToast(error.message || 'Could not open Settings.', 'error');
      }
    },
    [location.pathname, navigate, views, pushToast],
  );

  // No default section: an ordinary open lands on the category list on a phone
  // and on the window's usual section on a wide screen. An explicit section, a
  // search result and a `/settings#section` bookmark all still open directly.
  const openSettings = useCallback(
    (section) => {
      if (compact) setRequest({ section: section || null, locationKey: location.key });
      else openWindow(section);
    },
    [compact, location.key, openWindow],
  );

  // A bookmark becomes the window on a wide screen and the screen on a phone,
  // once per visit to the URL. The phone's is recorded as a request so it stays
  // open, like any other, if the window then grows.
  const handled = useRef(null);
  useEffect(() => {
    if (!routed || handled.current === location.key) return;
    if (compact) {
      handled.current = location.key;
      setRequest({ section: location.hash.slice(1) || null, locationKey: location.key });
      return;
    }
    // Wait for what is already open, or a reload would open a second window
    // beside the one the desk is about to draw.
    if (!views.desktopId || !views.loaded) return;
    handled.current = location.key;
    openWindow(location.hash.slice(1) || 'general', { replace: true });
  }, [routed, compact, views.desktopId, views.loaded, location.key, location.hash, openWindow]);

  // A screen opened on a phone stays open until it is closed, even when the
  // window grows past the phone width — a tablet turned sideways must not lose
  // a half-filled form. At that width it draws as a popup over the page.
  const screenOpen = (routed && compact) || request?.locationKey === location.key;
  const close = () => {
    setRequest(null);
    if (routed) navigate('/', { replace: true });
  };

  return (
    <SettingsContext.Provider
      value={{
        openSettings,
        // Only the screen covers the page. The window is one window among
        // others, so it does not hold back the welcome guide or anything else
        // that waits for the page to be free.
        settingsOpen: screenOpen,
        sectionRequest,
      }}
    >
      {children}
      {/* `explicit` says whether a section was actually asked for: a phone
          opens the category list when it was not, and that section when it was. */}
      {screenOpen && (
        <Settings
          initialSection={
            routed ? location.hash.slice(1) || 'general' : request.section || 'appearance'
          }
          explicit={routed ? Boolean(location.hash.slice(1)) : Boolean(request.section)}
          onClose={close}
        />
      )}
    </SettingsContext.Provider>
  );
}
