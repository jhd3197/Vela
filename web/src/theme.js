// The look: which base the dashboard is in, and which theme is painting it.
//
// Two settings, deliberately separate. `theme` is the base -- light or dark --
// and is the choice a person makes by time of day or by preference. `theme_id`
// is the theme, which decides what light and dark are made of. Apps keep
// receiving only the base, and nothing else about a theme reaches them.
const KEY = 'vela-theme';
const THEME_KEY = 'vela-theme-id';
import { readLocal, writeLocal } from './storage.js';
import { applyTheme, cacheTheme, cachedTheme, clearTheme, STOCK } from './design/apply.js';

export { STOCK };

export function getTheme() {
  return readLocal(KEY) === 'dark' ? 'dark' : 'light';
}

export function getThemeId() {
  return readLocal(THEME_KEY) || STOCK;
}

/** Point the phone's own chrome at the ground the dashboard is drawing. */
function paintBrowserChrome() {
  const meta = document.querySelector('meta[name="theme-color"]');
  if (!meta) return;
  const ground = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
  if (ground) meta.setAttribute('content', ground);
}

/** Switch base, keeping whichever theme is painting it. */
export function applyBase(base) {
  const cached = cachedTheme();
  if (cached && cached.slug !== STOCK && cached.tokens?.[base]) {
    applyTheme({ base, tokens: cached.tokens[base], other: cached.tokens[other(base)] });
  } else {
    clearTheme(base);
  }
  paintBrowserChrome();
}

const other = (base) => (base === 'dark' ? 'light' : 'dark');

export function setTheme(base) {
  // A browser that refuses to keep it still shows it for this session.
  writeLocal(KEY, base);
  applyBase(base);
}

/**
 * Paint a theme and remember it.
 *
 * `theme` is the document the server holds. A theme that does not carry the
 * base in use falls back to the one it does carry, which is what lets a
 * light-only theme be selected by somebody sitting in dark.
 */
export function setThemeDocument(theme, base = getTheme()) {
  if (!theme || theme.slug === STOCK) {
    writeLocal(THEME_KEY, STOCK);
    cacheTheme(null);
    clearTheme(base);
    paintBrowserChrome();
    return;
  }
  const chosen = theme.tokens?.[base] ? base : theme.bases?.[0];
  writeLocal(THEME_KEY, theme.slug);
  cacheTheme({ slug: theme.slug, bases: theme.bases, tokens: theme.tokens });
  applyTheme({
    base: chosen,
    tokens: theme.tokens?.[chosen],
    other: theme.tokens?.[other(chosen)],
  });
  paintBrowserChrome();
}

/**
 * The first paint, before React mounts.
 *
 * The base and the theme both come from the last time this browser saw them, so
 * the page opens in the colours it closed in. `ThemeSync` corrects both once
 * settings load, as it already did for the base.
 */
export function initTheme() {
  applyBase(getTheme());
}
