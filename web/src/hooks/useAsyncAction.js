import { useCallback, useEffect, useRef, useState } from 'react';

// User-triggered work only: no automatic retries, and a pending action cannot
// be submitted twice. Keep confirmations and permission checks in the caller.
export function useAsyncAction() {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);
  const session = useRef(null);
  useEffect(() => {
    const scope = { active: true, pending: false };
    session.current = scope;
    return () => { scope.active = false; };
  }, []);

  const run = useCallback(async (action) => {
    const scope = session.current;
    if (!scope?.active || scope.pending) return undefined;
    scope.pending = true;
    setPending(true);
    setError(null);
    try {
      const value = await action();
      return scope.active ? { value } : undefined;
    } catch (failure) {
      if (scope.active) setError(failure);
      return undefined;
    } finally {
      scope.pending = false;
      if (scope.active) setPending(false);
    }
  }, []);

  return { run, pending, error };
}
