// One keyboard-shortcut owner for the shell's global shortcuts. A single
// keydown listener on `window` drives them so no page has to grow its own
// global handler. Which keys mean what is written down once, in
// `shell/keys.js`, which the shortcut sheet reads too; the window shortcuts are
// answered by the window host, which is where the windows are. Plain-key
// shortcuts are ignored while an editable target has focus (inputs, textareas,
// selects, contenteditable), and the listener cannot see keys pressed inside an
// app's iframe because those events go to the frame's own document, not this
// window.
import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useApps } from './store.jsx';
import useOpenApp from './desktops/useOpenApp.js';
import { coreById, isCoreId } from './navigation.js';
import { useSettingsPopup } from './components/SettingsProvider.jsx';
import { useAppsOverlay } from './desktops/AppsOverlay.jsx';
import { useDesktops } from './desktops/DesktopsProvider.jsx';
import { isEditable, matches } from './shell/keys.js';

const LAUNCHPAD = '/apps';

// Where the user was before the Launchpad opened, so Escape and a second
// Ctrl+Space put them back rather than guessing at `/`.
let launchpadReturn = '/';

export function launchpadReturnTo() {
  return launchpadReturn || '/';
}

export function useGlobalShortcuts() {
  const navigate = useNavigate();
  const location = useLocation();
  const { pinned } = useApps();
  const openApp = useOpenApp();
  const { openSettings } = useSettingsPopup();
  const { toggleApps } = useAppsOverlay();
  const { desktops, selectedId, select: selectDesktop } = useDesktops();
  const [shortcutsOpen, setShortcutsOpen] = useState(false);

  useEffect(() => {
    const openPinned = (index) => {
      const id = pinned[index];
      if (!id) return;
      if (isCoreId(id)) {
        const entry = coreById(id);
        if (!entry) return;
        if (entry.popup) openSettings();
        else navigate(entry.to, { state: { returnTo: location.pathname + location.search } });
      } else {
        openApp(id, { returnTo: location.pathname + location.search });
      }
    };

    // The next desktop, or the one before, wrapping. Choosing one shows its
    // desk, the same as choosing it in the rail's desktop menu.
    const stepDesktop = (direction) => {
      if (desktops.length < 2) return;
      const index = desktops.findIndex((desktop) => desktop.id === selectedId);
      const next = desktops[(index + direction + desktops.length) % desktops.length];
      if (!next) return;
      selectDesktop(next.id);
      if (location.pathname !== '/') navigate('/');
    };

    const onKey = (event) => {
      if (event.defaultPrevented) return;
      // The All apps toggle is a deliberate modifier chord, so it works even
      // while a field has focus — including All apps' own search box.
      if (matches(event, 'all-apps')) {
        event.preventDefault();
        // All apps opens over the current page, so the toggle does not
        // navigate; the recorded return is still what a `/apps` link uses.
        if (location.pathname !== LAUNCHPAD) launchpadReturn = location.pathname + location.search;
        toggleApps();
        return;
      }
      // Ctrl+1..9 opens the matching pinned app; a modifier chord, so it fires
      // whatever has focus.
      if ((event.ctrlKey || event.metaKey) && !event.altKey && /^[1-9]$/.test(event.key)) {
        event.preventDefault();
        openPinned(Number(event.key) - 1);
        return;
      }
      // Ctrl+/ opens the shortcut sheet from anywhere.
      if ((event.ctrlKey || event.metaKey) && event.key === '/') {
        event.preventDefault();
        setShortcutsOpen((open) => !open);
        return;
      }
      // The remaining shortcuts yield to a field: there, Alt+Shift+arrows
      // select by word, and ? is a question mark.
      if (isEditable(event.target)) return;
      if (matches(event, 'next-desktop') || matches(event, 'previous-desktop')) {
        event.preventDefault();
        stepDesktop(matches(event, 'next-desktop') ? 1 : -1);
        return;
      }
      if (event.key === '?' && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        setShortcutsOpen((open) => !open);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [
    navigate,
    location.pathname,
    location.search,
    pinned,
    openApp,
    openSettings,
    toggleApps,
    desktops,
    selectedId,
    selectDesktop,
  ]);

  return { shortcutsOpen, closeShortcuts: () => setShortcutsOpen(false) };
}
