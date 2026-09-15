import { useSyncExternalStore } from 'react';
import { sharedViewport } from '../viewport.js';

const EMPTY = {
  left: 0,
  top: 0,
  width: 0,
  height: 0,
  layoutWidth: 0,
  layoutHeight: 0,
  scale: 1,
  measured: false,
  zoomed: false,
  keyboardInset: 0,
};

// The shared viewport state, for the few places that need the numbers rather
// than the CSS variables the same store publishes. The store keeps one frozen
// snapshot per change, so subscribing here re-renders only that consumer and
// only when a value it can see moved.
export default function useViewport() {
  return useSyncExternalStore(
    (notify) => sharedViewport()?.subscribe(notify) ?? (() => {}),
    () => sharedViewport()?.getState() ?? EMPTY,
    () => EMPTY,
  );
}
