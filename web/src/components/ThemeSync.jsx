import { useEffect } from 'react';
import { api } from '../api.js';
import { setTheme } from '../theme.js';

// Applies the server-persisted theme once settings load. The last-known theme
// from localStorage is already applied at boot, so this only flips the page
// when the server disagrees; on failure the local preference stands.
export default function ThemeSync() {
  useEffect(() => {
    let cancelled = false;
    api
      .getSettings()
      .then((settings) => {
        if (cancelled) return;
        if (settings?.theme === 'dark' || settings?.theme === 'light') setTheme(settings.theme);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);
  return null;
}
