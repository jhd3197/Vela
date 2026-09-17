// What every agent desktop is doing, in one small answer.
//
// Shared by the rail's desktop switcher and the open-views list, so a person
// looking at Desktop 1 can still see that Desktop 2 is working or that it needs
// them. One poll for the whole server rather than one per desktop: this is a
// badge, and a badge that cost a request per workspace would be a badge that
// costs more than it is worth.
//
// It stops while the tab is hidden, and a failure leaves the last answer on
// screen rather than blanking it — a dot that disappears because a request
// failed would read as "nothing is happening", which is the one thing it must
// never say by accident.
import { useEffect, useState } from 'react';
import { desktopsApi } from './desktopsApi.js';

const INTERVAL_MS = 4000;

export default function useAttention({ enabled = true } = {}) {
  const [state, setState] = useState({});

  useEffect(() => {
    if (!enabled) return undefined;
    let stopped = false;
    let timer = null;
    const tick = async () => {
      if (stopped) return;
      if (document.visibilityState === 'visible') {
        try {
          const answer = await desktopsApi.attention();
          if (!stopped) setState(answer.desktops || {});
        } catch {
          /* Keep what is on screen. */
        }
      }
      if (!stopped) timer = setTimeout(tick, INTERVAL_MS);
    };
    tick();
    const wake = () => {
      if (document.visibilityState === 'visible') {
        clearTimeout(timer);
        tick();
      }
    };
    document.addEventListener('visibilitychange', wake);
    return () => {
      stopped = true;
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', wake);
    };
  }, [enabled]);

  return state;
}

/** The one word a badge says, or null when there is nothing to say. */
export function attentionWord(entry) {
  if (!entry) return null;
  if (entry.needsYou) return entry.needsYou === 1 ? 'Needs you' : `${entry.needsYou} need you`;
  if (entry.working) return 'Working';
  if (entry.queued) return entry.queued === 1 ? '1 waiting' : `${entry.queued} waiting`;
  return null;
}
