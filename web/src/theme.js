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
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', theme === 'dark' ? '#16202f' : '#e4e7f5');
}

export function setTheme(theme) {
  // A browser that refuses to keep it still shows it for this session.
  writeLocal(KEY, theme);
  applyTheme(theme);
}

export function initTheme() {
  applyTheme(getTheme());
}
