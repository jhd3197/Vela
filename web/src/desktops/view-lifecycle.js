// One app view's life: its session, its bridge, and the end of both.
//
// This used to live inside the full-screen app page, which was fine while that
// page was the only thing that showed an app. A desktop shows apps in windows,
// and two copies of "open a session, attach a bridge, revoke on the way out"
// would be two places for the revoke to be forgotten. So the page and the
// window share this, and the chrome around it is what differs between them.
//
// What it deliberately does not own: where the window is, whether it is
// minimized, and whether it is on screen at all. Hiding a view is presentation;
// this is lifetime. Keeping them apart is what lets a minimized window keep its
// session, its unsaved text and its running app.
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import { createBridge } from '../bridge/host.js';

/**
 * Open an app session and attach a bridge to `frameRef`, for as long as
 * `enabled` holds.
 *
 * `contextRef` is a ref to a function rather than a value: the context depends
 * on the frame's measured box, which is not known until after it has rendered,
 * and re-creating the bridge every time the window moved would reload the app.
 *
 * Returns `{ session, ready, error, save, updateContext, clearError }`.
 */
export default function useAppFrame({
  appId,
  enabled,
  frameRef,
  contextRef,
  onDirty,
  onNavigate,
  onReady,
  onError,
}) {
  const [session, setSession] = useState(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const bridge = useRef(null);
  // Held in refs so a changed callback does not tear the bridge down and
  // reload the app underneath the person using it.
  const handlers = useRef({ onDirty, onNavigate, onReady, onError });
  handlers.current = { onDirty, onNavigate, onReady, onError };

  useEffect(() => {
    if (!enabled) {
      setSession(null);
      setReady(false);
      setError('');
      return undefined;
    }
    let disposed = false;
    api
      .openSession(appId)
      .then((value) => {
        if (disposed) {
          // The view went away while the session was being issued. Revoking it
          // here is the difference between a tidy close and a token that stays
          // valid for an hour because nobody was left to end it.
          fetch('/api/app/session', {
            method: 'DELETE',
            headers: { Authorization: `Bearer ${value.token}` },
          }).catch(() => {});
        } else {
          setSession(value);
        }
      })
      .catch((failure) => {
        if (!disposed) setError(failure.message);
      });
    return () => {
      disposed = true;
    };
  }, [appId, enabled]);

  useEffect(() => {
    if (!session || !frameRef.current || !enabled) return undefined;
    const active = createBridge({
      frame: frameRef.current,
      session,
      context: contextRef.current(),
      onDirty: (state) => handlers.current.onDirty?.(state),
      onNavigate: () => handlers.current.onNavigate?.(),
      onReady: () => {
        setReady(true);
        handlers.current.onReady?.();
      },
      onError: (message) => {
        setError(message);
        handlers.current.onError?.(message);
      },
    });
    bridge.current = active;
    const update = () => active.updateContext(contextRef.current());
    const resize = new ResizeObserver(update);
    resize.observe(frameRef.current);
    const theme = new MutationObserver(update);
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => {
      active.close();
      bridge.current = null;
      resize.disconnect();
      theme.disconnect();
    };
    // `contextRef` and `frameRef` are refs; re-running on them would reload the
    // app every time the window moved.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, enabled]);

  const updateContext = useCallback(() => {
    bridge.current?.updateContext(contextRef.current());
  }, [contextRef]);

  const save = useCallback(async () => {
    if (!bridge.current) throw new Error('This app is not connected.');
    return bridge.current.save();
  }, []);

  return { session, ready, error, setError, save, updateContext };
}
