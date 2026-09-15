import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import LockScreen from './security/LockScreen.jsx';

const SecurityContext = createContext({
  status: null,
  refresh: () => Promise.resolve(null),
  apply: () => {},
});

export const useSecurity = () => useContext(SecurityContext);

// The engine owns lock state. This only mirrors it, reports genuine activity,
// and puts the lock screen over everything while the session is locked.
const ACTIVITY_INTERVAL = 20000;
const POLL_INTERVAL = 15000;

export default function SecurityProvider({ children }) {
  const [status, setStatus] = useState(null);
  // A session that went to the background is treated as covered until the
  // engine has answered again, so protected content is never revealed first.
  const [veiled, setVeiled] = useState(false);
  const enrolled = Boolean(status?.enrolled);
  const locked = Boolean(status?.locked);

  const refresh = useCallback(
    () =>
      api
        .getSecurity()
        .then((next) => {
          setStatus(next);
          setVeiled(false);
          return next;
        })
        .catch(() => null),
    [],
  );

  useEffect(() => {
    refresh();
    const onLocked = () =>
      setStatus((previous) => (previous ? { ...previous, locked: true } : previous));
    addEventListener('vela:locked', onLocked);
    return () => removeEventListener('vela:locked', onLocked);
  }, [refresh]);

  // Coming back from the background must not reset the timer or the lock; it
  // only asks the engine what the state is now.
  const enrolledRef = useRef(enrolled);
  enrolledRef.current = enrolled;
  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') {
        if (enrolledRef.current) setVeiled(true);
      } else {
        refresh();
      }
    };
    addEventListener('visibilitychange', onVisibility);
    return () => removeEventListener('visibilitychange', onVisibility);
  }, [refresh]);

  useEffect(() => {
    if (!enrolled || locked) return undefined;
    const timer = setInterval(refresh, POLL_INTERVAL);
    return () => clearInterval(timer);
  }, [enrolled, locked, refresh]);

  // Only deliberate interaction defers the inactivity lock.
  useEffect(() => {
    if (!enrolled || locked) return undefined;
    let last = 0;
    const report = () => {
      const now = Date.now();
      if (now - last < ACTIVITY_INTERVAL) return;
      last = now;
      api.reportActivity().catch(() => {});
    };
    addEventListener('pointerdown', report, { passive: true });
    addEventListener('keydown', report, { passive: true });
    addEventListener('vela:activity', report);
    return () => {
      removeEventListener('pointerdown', report);
      removeEventListener('keydown', report);
      removeEventListener('vela:activity', report);
    };
  }, [enrolled, locked]);

  const value = { status, refresh, apply: setStatus };

  return (
    <SecurityContext.Provider value={value}>
      {children}
      {enrolled && (locked || veiled) && (
        <LockScreen status={status} checking={veiled && !locked} onUnlocked={setStatus} />
      )}
    </SecurityContext.Provider>
  );
}

// Work inside an app frame is real activity the host cannot see directly. The
// app view reports it through this rather than by reaching into the frame.
export function reportAppActivity() {
  dispatchEvent(new Event('vela:activity'));
}
