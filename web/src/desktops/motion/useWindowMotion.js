// Minimizing and restoring, with the warp where one is possible.
//
// This is the seam between a decision and its decoration, and it is written so
// the decision always wins. Minimize means the window is put away: that happens
// whether or not a picture could be taken, whether or not the tab is visible,
// whether or not the animation ever draws a frame. The warp is something that
// may accompany it.
//
// Nothing here touches an app's process, its bridge session or an agent's task.
// A minimized remote view is a view the owner is not looking at; the page is
// still open on the server, the agent is still observing it and the run is still
// going. Hiding something and stopping it are different, and a prototype that
// wired one button to both is where that confusion comes from.
import { useCallback, useEffect, useRef, useState } from 'react';
import { GENIE } from './genie-preset.js';
import { elapsedFor, reversalDuration } from './genie-geometry.js';
import { CAPABILITY, capabilityFor, loadFrame } from './frame-source.js';
import {
  MOTION,
  cancelMotion,
  initialMotion,
  isAnimating,
  requestMotion,
  settleMotion,
} from './motion-state.js';

/** Whether the person has asked for less movement. Watched, not read once. */
function useReducedMotion() {
  const [reduced, setReduced] = useState(() =>
    typeof matchMedia === 'function'
      ? matchMedia('(prefers-reduced-motion: reduce)').matches
      : false,
  );
  useEffect(() => {
    if (typeof matchMedia !== 'function') return undefined;
    const query = matchMedia('(prefers-reduced-motion: reduce)');
    const onChange = (event) => setReduced(event.matches);
    query.addEventListener?.('change', onChange);
    return () => query.removeEventListener?.('change', onChange);
  }, []);
  return reduced;
}

export default function useWindowMotion({ desktopId, desktop, area }) {
  const [motions, setMotions] = useState({});
  const [run, setRun] = useState(null);
  const reduced = useReducedMotion();
  // The frame and the abort handle of whatever is in flight. Held in a ref so
  // releasing them does not depend on a render happening first.
  const active = useRef(null);
  // The shape on screen, written every frame by the overlay and read when
  // somebody reverses. A ref because a render per frame would be the most
  // expensive thing in the animation.
  const live = useRef({});

  const release = useCallback(() => {
    const held = active.current;
    active.current = null;
    if (!held) return;
    held.controller?.abort();
    held.frame?.release?.();
  }, []);

  useEffect(() => release, [release]);

  const progressed = useCallback((viewId, collapse) => {
    live.current[viewId] = collapse;
  }, []);

  const settle = useCallback(
    (generation) => {
      setMotions((current) => {
        const entry = Object.entries(current).find(
          ([, motion]) => motion.generation === generation,
        );
        if (!entry) return current;
        return { ...current, [entry[0]]: settleMotion(entry[1], generation) };
      });
      setRun((current) => (current?.generation === generation ? null : current));
      release();
    },
    [release],
  );

  /**
   * Put a window away, or bring it back.
   *
   * `apply` is what actually changes the layout, and it is called immediately
   * rather than when the animation ends. That ordering is the whole design: the
   * person asked for the window to be minimized, so it is minimized, and the
   * picture catches up.
   */
  const animate = useCallback(
    async (view, direction, { icon, bounds, apply }) => {
      apply?.();

      const stored = motions[view.id] || initialMotion(Boolean(view.window?.minimized));
      // Where it actually is, not where the last settled state said it was.
      const previous = isAnimating(stored)
        ? { ...stored, collapse: live.current[view.id] ?? stored.collapse }
        : stored;
      // A reversal starts from the shape that is on screen and takes as long as
      // the distance that is left, so rapid minimize/restore never jumps.
      const durationMs = isAnimating(previous)
        ? reversalDuration(previous.collapse, { direction })
        : GENIE.durationMs;

      const { capability, reason } = capabilityFor(view, { desktop });
      if (reduced || capability !== CAPABILITY.REMOTE || !icon || !bounds || !area.width) {
        // No warp. The decision has already happened; this records that the
        // decoration did not, with the reason, so a person asking why gets an
        // answer rather than a shrug.
        setMotions((current) => ({
          ...current,
          [view.id]: {
            ...requestMotion(previous, direction, { durationMs: 0, reason: reason || 'reduced' }),
            state: direction === 'collapse' ? MOTION.MINIMIZED : MOTION.IDLE,
            collapse: direction === 'collapse' ? 1 : 0,
            direction: null,
          },
        }));
        return;
      }

      release();
      const controller = new AbortController();
      active.current = { controller, frame: null };
      const started = requestMotion(previous, direction, { durationMs, reason: null });
      setMotions((current) => ({ ...current, [view.id]: started }));

      const frame = await loadFrame(desktopId, view, { signal: controller.signal });
      if (!frame.ok || controller.signal.aborted) {
        // A picture that could not be taken is not a failure of the minimize.
        // The window is already where it was asked to be.
        settle(started.generation);
        return;
      }
      active.current = { controller, frame };
      setRun({
        generation: started.generation,
        viewId: view.id,
        direction,
        durationMs,
        // Where it starts from, converted so a reversal picks up mid-shape
        // rather than at an endpoint.
        startElapsed: elapsedFor(previous.collapse, { direction }),
        source: bounds,
        target: icon,
        frame,
      });
    },
    [area, desktop, desktopId, motions, reduced, release, settle],
  );

  /** Stop drawing and apply the state that was decided, for any reason at all. */
  const abandon = useCallback(
    (reason) => {
      setMotions((current) => {
        const next = { ...current };
        for (const [viewId, motion] of Object.entries(current)) {
          next[viewId] = cancelMotion(motion, reason);
        }
        return next;
      });
      setRun(null);
      release();
    },
    [release],
  );

  // Everything that means "the thing being illustrated is no longer what is on
  // screen": a different desktop, a resized work area, a density change, the
  // preference changing while it runs.
  useEffect(() => {
    abandon('the desktop changed');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [desktopId]);

  useEffect(() => {
    if (run) abandon('the layout changed');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [area.width, area.height, reduced]);

  return {
    motions,
    run,
    reduced,
    animate,
    settle,
    progressed,
    abandon,
    /** Whether this window should be drawn as a window at the moment. */
    presented: useCallback(
      (viewId) => (motions[viewId]?.state ?? MOTION.IDLE) !== MOTION.MINIMIZED,
      [motions],
    ),
    /** The view the overlay is currently standing in for, if any. */
    animatingViewId: run?.viewId || null,
  };
}
