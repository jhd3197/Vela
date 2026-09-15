import { useSyncExternalStore } from 'react';

// Whether the pattern draws a visible trail. This is a display preference for
// this browser only: no secret, verifier or unlock state is ever kept here.
const KEY = 'vela.pattern-trail';
const listeners = new Set();

function read() {
  try {
    return localStorage.getItem(KEY) !== 'off';
  } catch {
    // Private mode or blocked storage: show the trail, which is the default.
    return true;
  }
}

export function setShowTrail(on) {
  try {
    localStorage.setItem(KEY, on ? 'on' : 'off');
  } catch {
    // Nothing to persist; the change still applies to this page.
  }
  listeners.forEach((notify) => notify());
}

export function useShowTrail() {
  return useSyncExternalStore(
    (notify) => {
      listeners.add(notify);
      addEventListener('storage', notify);
      return () => {
        listeners.delete(notify);
        removeEventListener('storage', notify);
      };
    },
    read,
    () => true,
  );
}
