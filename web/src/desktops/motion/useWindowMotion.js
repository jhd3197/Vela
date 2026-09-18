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
import { collapseTransform } from './genie-fallback.js';
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
  // The container motion, for the windows there is no picture of. It is the
  // live window travelling to its icon rather than a canvas standing in for it,
  // so it carries no frame and never unmounts what it is moving.
  const [fallback, setFallback] = useState(null);
  const fallbackTimers = useRef({ frame: 0, done: 0, hidden: null });
  // The container motion has no per-frame callback to report from — a CSS
  // transition runs on its own — so where it has got to is read from the clock
  // when somebody reverses it. Linear against an eased transition, which is an
  // approximation of a shape rather than of a decision, and is what keeps a
  // minimize interrupted halfway from snapping back in a sixteenth of the time.
  const travelling = useRef(null);
  const reduced = useReducedMotion();
  // The frame and the abort handle of whatever is in flight. Held in a ref so
  // releasing them does not depend on a render happening first.
  const active = useRef(null);
  // The shape on screen, written every frame by the overlay and read when
  // somebody reverses. A ref because a render per frame would be the most
  // expensive thing in the animation.
  const live = useRef({});

  const release = useCallback(() => {
    cancelAnimationFrame(fallbackTimers.current.frame);
    clearTimeout(fallbackTimers.current.done);
    if (fallbackTimers.current.hidden) {
      document.removeEventListener('visibilitychange', fallbackTimers.current.hidden);
    }
    fallbackTimers.current = { frame: 0, done: 0, hidden: null };
    travelling.current = null;
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

  /** Where this window's shape actually is, whichever kind of motion drew it. */
  const shapeOf = useCallback((viewId) => {
    const flight = travelling.current;
    if (flight?.viewId === viewId) {
      const k = Math.min(
        1,
        Math.max(0, (performance.now() - flight.startedAt) / flight.durationMs),
      );
      return flight.direction === 'collapse' ? k : 1 - k;
    }
    return live.current[viewId];
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
      setFallback((current) => (current?.generation === generation ? null : current));
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
        ? { ...stored, collapse: shapeOf(view.id) ?? stored.collapse }
        : stored;
      // A reversal starts from the shape that is on screen and takes as long as
      // the distance that is left, so rapid minimize/restore never jumps.
      const durationMs = isAnimating(previous)
        ? reversalDuration(previous.collapse, { direction })
        : GENIE.durationMs;

      const { capability, reason } = capabilityFor(view, { desktop });
      const settled = (why) => {
        // No motion at all. The decision has already happened; this records
        // that the decoration did not, with the reason, so a person asking why
        // gets an answer rather than a shrug.
        setMotions((current) => ({
          ...current,
          [view.id]: {
            ...requestMotion(previous, direction, { durationMs: 0, reason: why }),
            state: direction === 'collapse' ? MOTION.MINIMIZED : MOTION.IDLE,
            collapse: direction === 'collapse' ? 1 : 0,
            direction: null,
          },
        }));
      };

      if (reduced) {
        settled('reduced');
        return;
      }
      // A hidden tab delivers no animation frames and throttles its timers to
      // minutes, so a motion started in one would not finish for as long as
      // nobody looked — leaving a window shrunk into its icon and unclickable.
      // The decision has already been applied; there is simply nothing to show
      // somebody who is not there.
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
        settled('that desktop is not on screen');
        return;
      }
      if (!icon || !bounds || !area.width) {
        settled(reason || 'there is nowhere on screen to fly to');
        return;
      }

      if (capability !== CAPABILITY.REMOTE) {
        // The container motion. There is no picture of this window, so the
        // window itself travels — still mounted, still running, still holding
        // whatever was typed into it.
        const collapsed = collapseTransform(bounds, icon);
        if (!collapsed) {
          settled(reason || 'there is nowhere on screen to fly to');
          return;
        }
        release();
        const started = requestMotion(previous, direction, { durationMs, reason });
        setMotions((current) => ({ ...current, [view.id]: started }));
        travelling.current = {
          viewId: view.id,
          direction,
          durationMs,
          startedAt: performance.now(),
        };
        // A transition needs a value to leave before it has one to arrive at.
        // Only one case actually lacks one: a window being brought back is not
        // on screen at all, so it is put at its icon first and released on the
        // next frame. A window being put away is already drawn where it is, and
        // a reversal is mid-transition — retargeting either of those in one
        // commit is what makes them continue from where they are instead of
        // jumping to an endpoint and easing from there.
        const staged = direction === 'expand' && !isAnimating(previous);
        const run = {
          generation: started.generation,
          viewId: view.id,
          direction,
          durationMs,
          bounds,
          collapsed,
          phase: staged ? 'start' : 'end',
        };
        setFallback(run);
        // Settled on the clock rather than on `transitionend`: a transition on
        // a window nobody is looking at may never report finishing, and the
        // window would stay half-shrunk and unclickable.
        const finish = () => {
          fallbackTimers.current.done = setTimeout(() => settle(started.generation), durationMs);
        };
        // And if the tab is hidden part-way through, the run settles at once
        // rather than waiting for a throttled timer that may be minutes away.
        const onHidden = () => {
          if (document.visibilityState === 'hidden') settle(started.generation);
        };
        document.addEventListener('visibilitychange', onHidden);
        fallbackTimers.current.hidden = onHidden;
        if (!staged) {
          finish();
          return;
        }
        fallbackTimers.current.frame = requestAnimationFrame(() => {
          setFallback((current) =>
            current?.generation === started.generation ? { ...current, phase: 'end' } : current,
          );
          finish();
        });
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
    [area, desktop, desktopId, motions, reduced, release, settle, shapeOf],
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
      setFallback(null);
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
    fallback,
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
    /** The view the canvas overlay is currently standing in for, if any. */
    animatingViewId: run?.viewId || null,
  };
}
