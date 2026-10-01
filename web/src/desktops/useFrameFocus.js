// Clicking inside an app brings its window forward.
//
// A click on a window's chrome selects it through the frame's own pointerdown.
// A click inside the app does not: the pointer event goes to the app's
// document, and the dashboard never hears about it. So the window you are
// typing into could stay behind the one you were in before, and the top bar
// and the rail would keep naming the wrong app.
//
// What the dashboard *can* see is where its own focus went. Focus leaving the
// page for an iframe blurs the window; focus moving from one app's iframe to
// another fires nothing at all, but `document.activeElement` still changes. So
// the first is caught by the blur, and the second by a light watch that runs
// only while focus is inside an app and stops the moment it comes back.
import { useEffect, useRef } from 'react';

/** How often to look while focus is inside an app. Cheap, and quick enough to feel immediate. */
const WATCH_MS = 250;

/** The window a focused iframe belongs to, if it is one of this host's windows. */
export function viewIdForFrame(element, host) {
  if (!element || element.tagName !== 'IFRAME' || !host) return null;
  const frame = element.closest?.('[data-view-id]');
  if (!frame || !host.contains(frame)) return null;
  return frame.getAttribute('data-view-id') || null;
}

export default function useFrameFocus(hostRef, onFocusView) {
  // Read at the moment of use, so a new callback does not restart the watch.
  const callback = useRef(onFocusView);
  callback.current = onFocusView;

  useEffect(() => {
    let timer = 0;
    let last = null;

    const check = () => {
      const active = document.activeElement;
      if (active?.tagName !== 'IFRAME') {
        stop();
        return;
      }
      if (active === last) return;
      last = active;
      const viewId = viewIdForFrame(active, hostRef.current);
      if (viewId) callback.current?.(viewId);
    };

    const stop = () => {
      clearInterval(timer);
      timer = 0;
      last = null;
    };

    const onBlur = () => {
      // The browser moves focus after the blur, so the check waits one turn.
      setTimeout(() => {
        check();
        if (!timer && document.activeElement?.tagName === 'IFRAME') {
          timer = setInterval(check, WATCH_MS);
        }
      }, 0);
    };

    addEventListener('blur', onBlur);
    addEventListener('focus', stop);
    return () => {
      removeEventListener('blur', onBlur);
      removeEventListener('focus', stop);
      stop();
    };
  }, [hostRef]);
}
