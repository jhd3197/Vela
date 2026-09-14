// Theme preference: "light" (default, matches the Apps/Automations
// prototypes) or "dark" (the Home dashboard prototype). Persisted locally;
// applied as data-theme on <html> so every CSS variable flips at once.
const KEY = 'vela-theme';

export function getTheme() {
  try {
    return localStorage.getItem(KEY) === 'dark' ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', theme === 'dark' ? '#161826' : '#e4e7f5');
}

export function setTheme(theme) {
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // Private mode etc. — theme still applies for this session.
  }
  applyTheme(theme);
}

export function initTheme() {
  applyTheme(getTheme());
}
