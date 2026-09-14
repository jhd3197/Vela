export const WELCOME_KEY = 'vela.welcome.v1';
let dismissed = false;

export function welcomeDismissed() {
  try {
    return dismissed || localStorage.getItem(WELCOME_KEY) === 'done';
  } catch {
    return dismissed;
  }
}

export function dismissWelcome() {
  dismissed = true;
  try {
    localStorage.setItem(WELCOME_KEY, 'done');
  } catch {
    // Private/blocked storage still remembers dismissal for this page session.
  }
}

// Only share the authenticated server origin, never the current URL's tokens,
// app path or query string. Local-only servers cannot be reached by a phone.
export function phoneSetupUrl(origin, remote) {
  if (!remote) return null;
  try {
    const url = new URL(origin);
    const host = url.hostname.toLowerCase();
    if (
      url.protocol !== 'https:' ||
      url.username ||
      url.password ||
      host === 'localhost' ||
      host.endsWith('.localhost') ||
      host === '[::1]' ||
      host === '[::]' ||
      host === '0.0.0.0' ||
      host.startsWith('127.')
    )
      return null;
    return new URL('/setup', url.origin).href;
  } catch {
    return null;
  }
}

export function isIOSSafari(ua = navigator.userAgent) {
  // iOS browsers and in-app webviews can contain Safari in their UA too.
  return (
    /Version\/[\d.]+.*Safari\//.test(ua) &&
    !/CriOS|FxiOS|EdgiOS|OPiOS|DuckDuckGo|YaBrowser|GSA\/|FBAN|FBAV|Instagram|Line\//i.test(ua)
  );
}
