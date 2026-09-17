// What a desktop's agent is doing, as it happens.
//
// A numbered stream with a cursor rather than a socket. Reconnecting means
// asking for everything after the last number this viewer saw, so a closed
// laptop, a dropped Wi-Fi connection or a switched tab costs nothing — and,
// more importantly, so none of this owns the task. The run belongs to the
// server; this is a reader.
//
// Two honesty rules shape it.
//
// A viewer that has fallen further behind than the stream keeps is told there
// is a gap and shown a fresh snapshot of the task, rather than being handed a
// partial list that reads like the whole story. And while the tab is hidden it
// stops polling: a background tab asking every second for hours is a cost to
// the person's machine for information nobody is reading.
import { useCallback, useEffect, useRef, useState } from 'react';
import { desktopsApi } from './desktopsApi.js';

/** How often to ask while something is happening, and while nothing is. */
const BUSY_MS = 1200;
const IDLE_MS = 5000;

/** Activity kept in view. Older steps scroll out of the record, not out of it. */
const KEEP_EVENTS = 120;

export default function useAgentEvents(desktopId, { enabled = true } = {}) {
  const [events, setEvents] = useState([]);
  const [tasks, setTasks] = useState({ runs: [], active: null, keepingHistory: true });
  const [approvals, setApprovals] = useState([]);
  const [error, setError] = useState(null);
  const [gap, setGap] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const cursor = useRef(0);
  const timer = useRef(null);
  const alive = useRef(true);
  // Read inside the polling loop without making the loop depend on the state
  // it sets, which would restart it on every event.
  const tasksRef = useRef(tasks);
  tasksRef.current = tasks;

  const refresh = useCallback(async () => {
    if (!desktopId) return;
    try {
      const [stream, snapshot, waiting] = await Promise.all([
        desktopsApi.events(desktopId, cursor.current),
        desktopsApi.tasks(desktopId),
        desktopsApi.approvals(desktopId),
      ]);
      if (!alive.current) return;
      if (stream.gap) setGap(true);
      cursor.current = stream.cursor ?? cursor.current;
      if (stream.events.length) {
        setEvents((previous) => [...previous, ...stream.events].slice(-KEEP_EVENTS));
      }
      // The task list is the snapshot the stream is checked against. When a gap
      // happens this is what is still true; the activity list is what is not.
      setTasks(snapshot);
      setApprovals(waiting.approvals || []);
      setError(null);
    } catch (problem) {
      if (alive.current) setError(problem);
    } finally {
      if (alive.current) setLoaded(true);
    }
  }, [desktopId]);

  useEffect(() => {
    alive.current = true;
    if (!enabled || !desktopId) return undefined;
    cursor.current = 0;
    setEvents([]);
    setGap(false);
    setLoaded(false);

    let stopped = false;
    const tick = async () => {
      if (stopped) return;
      if (document.visibilityState === 'visible') await refresh();
      if (stopped) return;
      const busy =
        document.visibilityState === 'visible' &&
        Boolean(
          tasksRef.current.active &&
          !['succeeded', 'failed', 'cancelled', 'interrupted', 'outcome_unknown'].includes(
            tasksRef.current.active.state,
          ),
        );
      timer.current = setTimeout(tick, busy ? BUSY_MS : IDLE_MS);
    };
    const wake = () => {
      if (document.visibilityState === 'visible') {
        clearTimeout(timer.current);
        tick();
      }
    };
    tick();
    document.addEventListener('visibilitychange', wake);
    return () => {
      stopped = true;
      alive.current = false;
      clearTimeout(timer.current);
      document.removeEventListener('visibilitychange', wake);
    };
  }, [desktopId, enabled, refresh]);

  return {
    events,
    tasks,
    approvals,
    error,
    gap,
    loaded,
    refresh,
    // Acknowledging a gap is the viewer saying it has read the snapshot. It
    // does not recover the missing activity, and nothing here pretends to.
    clearGap: () => setGap(false),
  };
}
