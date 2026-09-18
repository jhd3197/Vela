import { useEffect } from 'react';
import { api } from '../api.js';
import { getThemeId, setTheme, setThemeDocument, STOCK } from '../theme.js';

// Applies the server's look once settings load: the base, and the theme that
// decides what the base is made of.
//
// The last-known base and theme are already painted at boot from this browser's
// own cache, so this only changes the page when the server disagrees -- which
// is what stops a reload flashing the stock colours before the chosen ones
// arrive. On failure the local preference stands, because a dashboard that
// cannot reach its own engine should still look like itself.
export default function ThemeSync() {
  useEffect(() => {
    let cancelled = false;
    api
      .getSettings()
      .then(async (settings) => {
        if (cancelled) return;
        const base =
          settings?.theme === 'dark' || settings?.theme === 'light' ? settings.theme : null;
        if (base) setTheme(base);

        const slug = settings?.theme_id || STOCK;
        // The cache already painted this one; re-fetching it would repaint the
        // same colours and cost a request on every load.
        if (slug === getThemeId()) return;
        if (slug === STOCK) {
          setThemeDocument(null, base || undefined);
          return;
        }
        const document = await api.getTheme(slug).catch(() => null);
        if (!cancelled && document) setThemeDocument(document, base || undefined);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);
  return null;
}
