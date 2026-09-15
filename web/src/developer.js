import { useSyncExternalStore } from 'react';

// "Show developer tools": one browser-local presentation preference, shared by
// navigation, settings, search, app menus and app details.
//
// It decides what this browser shows, never what anything is allowed to do.
// Grants, credentials, app lifecycle and every backend check are unaffected by
// it, so turning it on cannot widen access and turning it off cannot hide a
// required decision. A phone does not inherit the developer's desktop choice
// because the value never leaves this origin's local storage.
const KEY = 'vela-developer-tools';

const listeners = new Set();
let fallback = false;
let storageDenied = false;

// Anything other than the stored "on" — absent, corrupt, or written by a
// future version — reads as off.
function read() {
  try {
    return localStorage.getItem(KEY) === 'on';
  } catch {
    storageDenied = true;
    return fallback;
  }
}

let value = read();

function announce(next) {
  if (next === value) return;
  value = next;
  for (const listener of listeners) listener();
}

export function getDeveloperTools() {
  return value;
}

export function setDeveloperTools(on) {
  const next = Boolean(on);
  fallback = next;
  try {
    localStorage.setItem(KEY, next ? 'on' : 'off');
  } catch {
    // Private mode or blocked site data: the choice still applies to this tab.
    storageDenied = true;
  }
  announce(next);
}

// True when the choice cannot outlive this tab, so the settings panel can say
// so instead of silently forgetting it.
export function developerToolsPersist() {
  return !storageDenied;
}

export function subscribeDeveloperTools(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

if (typeof window !== 'undefined') {
  // Another tab on this origin changed the preference. A null key is a whole
  // storage clear, which also has to be picked up.
  window.addEventListener('storage', (event) => {
    if (event.key === KEY || event.key === null) announce(read());
  });
}

// Reactive read. Components re-render on a change without the dashboard
// reloading, remounting an open app, or discarding an unsent message.
export function useDeveloperTools() {
  return useSyncExternalStore(subscribeDeveloperTools, getDeveloperTools, getDeveloperTools);
}
