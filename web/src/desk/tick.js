// One poll tick for the whole desk.
//
// Origin: ServerKit `frontend/src/hooks/usePolling.js` (MIT, same owner). The
// scheduler is inlined here (ServerKit keeps it in `utils/pollScheduler.js`)
// and the two properties worth keeping are kept: a new request never starts
// before the previous one comes back, and a hidden tab does not poll at all.
//
// The desk polls from `DeskDataProvider`, not from each widget, so a board
// with four system widgets still makes one request per source.
import { useCallback, useEffect, useRef, useState } from 'react';

const hidden = () => typeof document !== 'undefined' && document.visibilityState === 'hidden';

/**
 * A counter that increases every `intervalMs` while the tab is visible, and
 * once immediately when it becomes visible again after being hidden. Widgets
 * that derive something from the clock rather than from the server (relative
 * times, staleness) can depend on this instead of holding their own timer.
 */
export function useDeskTick(intervalMs, { enabled = true } = {}) {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!enabled || !intervalMs) return undefined;
    let timer;
    const bump = () => setTick((value) => value + 1);
    const schedule = () => {
      clearTimeout(timer);
      if (hidden()) return;
      timer = setTimeout(() => {
        bump();
        schedule();
      }, intervalMs);
    };
    const onVisibility = () => {
      if (hidden()) {
        clearTimeout(timer);
        return;
      }
      bump();
      schedule();
    };
    schedule();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [enabled, intervalMs]);

  return tick;
}

/**
 * Keep something fresh while the user is looking at it. `callback` is held in
 * a ref, so an inline arrow re-created every render does not restart the
 * timer — the usual reason a hand-rolled poller silently polls far faster than
 * its stated interval. Returns a stable `refresh()` for "reload now".
 */
export function usePolling(callback, intervalMs, { enabled = true, immediate = true } = {}) {
  const callbackRef = useRef(callback);
  callbackRef.current = callback;
  const runRef = useRef(null);

  useEffect(() => {
    if (!enabled || !intervalMs) {
      runRef.current = null;
      return undefined;
    }
    let stopped = false;
    let inFlight = false;
    let timer;

    const schedule = () => {
      clearTimeout(timer);
      if (stopped || hidden()) return;
      timer = setTimeout(run, intervalMs);
    };
    async function run() {
      if (stopped || inFlight) return;
      inFlight = true;
      try {
        await callbackRef.current?.();
      } catch {
        // A failed poll is the caller's to surface; the schedule carries on.
      } finally {
        inFlight = false;
        schedule();
      }
    }
    runRef.current = run;

    const onVisibility = () => {
      if (hidden()) {
        clearTimeout(timer);
        return;
      }
      run();
    };
    if (immediate) run();
    else schedule();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      stopped = true;
      clearTimeout(timer);
      runRef.current = null;
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [enabled, intervalMs, immediate]);

  return useCallback(() => {
    if (runRef.current) return runRef.current();
    return callbackRef.current?.();
  }, []);
}
