// One door for browser storage.
//
// Every call site had grown the same try/catch, because reading or writing
// either area throws outright in private mode, with site data blocked, and
// when the quota is full — and a dashboard that throws while reading a
// preference is worse than one that forgets it. The rule here is the one
// `theme.js` already followed: a failed read is the caller's fallback, a
// failed write is `false`, and the value still applies to this page session.
//
// Keep this file and `clipboard.js` the only places that name a browser
// storage API; `web/scripts/ratchets/browser-boundary.mjs` enforces it.

const areas = {
  local: () => localStorage,
  session: () => sessionStorage,
};

function read(area, key, fallback) {
  try {
    const value = areas[area]().getItem(key);
    return value === null ? fallback : value;
  } catch {
    return fallback;
  }
}

function write(area, key, value) {
  try {
    areas[area]().setItem(key, String(value));
    return true;
  } catch {
    return false;
  }
}

function remove(area, key) {
  try {
    areas[area]().removeItem(key);
    return true;
  } catch {
    return false;
  }
}

/** The stored string, or `fallback` when absent or unreadable. */
export const readLocal = (key, fallback = null) => read('local', key, fallback);
/** `true` when the value was stored; `false` when the browser refused. */
export const writeLocal = (key, value) => write('local', key, value);
export const removeLocal = (key) => remove('local', key);

export const readSession = (key, fallback = null) => read('session', key, fallback);
export const writeSession = (key, value) => write('session', key, value);
export const removeSession = (key) => remove('session', key);

/**
 * Stored JSON, or `fallback` when the key is absent, storage is unreadable, or
 * the value is not JSON any more — an old shape left by an earlier version is
 * the same non-event as a missing key.
 */
export function readJson(key, fallback = null, { area = 'local' } = {}) {
  const raw = read(area, key, null);
  if (raw === null) return fallback;
  try {
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export function writeJson(key, value, { area = 'local' } = {}) {
  try {
    return write(area, key, JSON.stringify(value));
  } catch {
    // A value with a cycle in it; nothing is stored and nothing throws.
    return false;
  }
}
