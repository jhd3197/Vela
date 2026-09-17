// One keyboard-shortcut owner for the whole shell. A single keydown listener
// on `window` drives the OS-level shortcuts so no page has to grow its own
// global handler. Plain-key shortcuts are ignored while an editable target has
// focus (inputs, textareas, selects, contenteditable), and the listener cannot
// see keys pressed inside an app's iframe because those events go to the
// frame's own document, not this window.
import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useApps } from './store.jsx';
import { coreById, isCoreId } from './navigation.js';
import { useSettingsPopup } from './components/SettingsProvider.jsx';
import { useAppsOverlay } from './desktops/AppsOverlay.jsx';

const LAUNCHPAD = '/apps';

// Where the user was before the Launchpad opened, so Escape and a second
// Ctrl+Space put them back rather than guessing at `/`.
let launchpadReturn = '/';

export function launchpadReturnTo() {
  return launchpadReturn || '/';
}

function isEditable(target) {
  if (!target) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return Boolean(target.isContentEditable);
}

// Ctrl+Space (Cmd+Space on a Mac) toggles the Launchpad. Space is
// `event.code === 'Space'`, stable across layouts.
function isLaunchpadToggle(event) {
  const withModifier = event.ctrlKey || event.metaKey;
  return withModifier && !event.altKey && !event.shiftKey && event.code === 'Space';
}

export function useGlobalShortcuts() {
  const navigate = useNavigate();
  const location = useLocation();
  const { pinned, openApp } = useApps();
  const { openSettings } = useSettingsPopup();
  const { toggleApps } = useAppsOverlay();
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

    const onKey = (event) => {
      if (event.defaultPrevented) return;
      // The Launchpad toggle is a deliberate modifier chord, so it works even
      // while a field has focus — including the Launchpad's own search box.
      if (isLaunchpadToggle(event)) {
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
      // The remaining shortcuts are plain keys, so they yield to a field.
      if (isEditable(event.target)) return;
      if (event.key === '?') {
        event.preventDefault();
        setShortcutsOpen((open) => !open);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [navigate, location.pathname, location.search, pinned, openApp, openSettings, toggleApps]);

  return { shortcutsOpen, closeShortcuts: () => setShortcutsOpen(false) };
}
