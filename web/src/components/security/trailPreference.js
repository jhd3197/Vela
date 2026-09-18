import { useSyncExternalStore } from 'react';
import { readLocal, writeLocal } from '../../storage.js';

// Whether the pattern draws a visible trail. This is a display preference for
// this browser only: no secret, verifier or unlock state is ever kept here.
const KEY = 'vela.pattern-trail';
const listeners = new Set();

function read() {
  // Private mode or blocked storage falls back to showing the trail, which is
  // the default.
  return readLocal(KEY) !== 'off';
}

export function setShowTrail(on) {
  // Nothing persisted is not a failure; the change still applies to this page.
  writeLocal(KEY, on ? 'on' : 'off');
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
