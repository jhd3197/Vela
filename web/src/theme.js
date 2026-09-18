// Theme preference: "light" (default, matches the Apps/Automations
// prototypes) or "dark" (the Home dashboard prototype). Persisted locally;
// applied as data-theme on <html> so every CSS variable flips at once.
const KEY = 'vela-theme';
import { readLocal, writeLocal } from './storage.js';

export function getTheme() {
  return readLocal(KEY) === 'dark' ? 'dark' : 'light';
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  // The phone paints its own chrome this colour, so it reads the ground the
  // dashboard is actually drawing rather than a copy of it: once a theme can
  // change `--bg`, a hard-coded pair here would leave a strip of the stock
  // look above every page. Read after the attribute moves, not before.
  const meta = document.querySelector('meta[name="theme-color"]');
  if (!meta) return;
  const ground = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
  if (ground) meta.setAttribute('content', ground);
}

export function setTheme(theme) {
  // A browser that refuses to keep it still shows it for this session.
  writeLocal(KEY, theme);
  applyTheme(theme);
}

export function initTheme() {
  applyTheme(getTheme());
}
