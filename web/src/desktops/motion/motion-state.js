// The lifecycle of one window's motion, as a state machine with no clock in it.
//
// Separate from the renderer, and deliberately so. Almost everything that goes
// wrong with an animation goes wrong here rather than in the drawing: a
// callback from a run that was cancelled two runs ago puts a window back that
// somebody has since closed; a rapid minimize/restore leaves two loops painting
// the same canvas; a tab that was hidden never delivers its final frame and the
// window stays hidden forever.
//
// Three rules, each of which is a test below.
//
// **Every run has a generation, and only the current one may finish.** A stale
// completion is discarded rather than applied. It cannot hide a view that has
// been restored since, and it cannot touch another desktop at all.
//
// **The last intent wins, from where things actually are.** Told to come back
// while it is a third of the way out, a window reverses from a third of the way
// out over a third of the time. It does not jump to an end and start again.
//
// **Motion is presentation and nothing else.** None of these states says
// anything about an app's process, a bridge session or an agent's task. A
// minimized window is a window that is not drawn; the thing it was drawing is
// still running, still observed and still doing what it was asked to.

/** Where a window's motion is. Never where its app or its task is. */
export const MOTION = Object.freeze({
  IDLE: 'idle',
  CAPTURING: 'capturing',
  MINIMIZING: 'minimizing',
  MINIMIZED: 'minimized',
  RESTORING: 'restoring',
});

/** The states in which something is actually being painted. */
export const RUNNING = Object.freeze([MOTION.MINIMIZING, MOTION.RESTORING]);

let counter = 0;

/** A fresh generation. Opaque, increasing, never reused in one page. */
export function nextGeneration() {
  counter += 1;
  return counter;
}

/** The starting state for a window nobody has animated yet. */
export function initialMotion(minimized = false) {
  return {
    state: minimized ? MOTION.MINIMIZED : MOTION.IDLE,
    generation: 0,
    direction: null,
    /** Where the shape is: 0 is the window, 1 is the icon. */
    collapse: minimized ? 1 : 0,
    startedAt: null,
    durationMs: 0,
    reason: null,
  };
}

/**
 * What the state becomes when somebody asks for a direction.
 *
 * `collapse` is read from whatever is on screen right now, which is what makes
 * a reversal continuous: asking to restore during a collapse starts expanding
 * from the shape that is already there.
 */
export function requestMotion(current, direction, { durationMs, now = 0, reason = null }) {
  const already =
    direction === 'collapse' ? current.state === MOTION.MINIMIZED : current.state === MOTION.IDLE;
  if (already && !RUNNING.includes(current.state)) {
    return { ...current, reason };
  }
  return {
    state: direction === 'collapse' ? MOTION.MINIMIZING : MOTION.RESTORING,
    generation: nextGeneration(),
    direction,
    collapse: current.collapse,
    startedAt: now,
    durationMs,
    reason,
  };
}

/**
 * What the state becomes when a run reports that it finished.
 *
 * A generation that is not the current one is ignored, which is the whole
 * mechanism: an old run's completion arriving after a new one has started
 * cannot decide what the window looks like.
 */
export function settleMotion(current, generation) {
  if (generation !== current.generation) return current;
  return {
    ...current,
    state: current.direction === 'collapse' ? MOTION.MINIMIZED : MOTION.IDLE,
    collapse: current.direction === 'collapse' ? 1 : 0,
    direction: null,
    startedAt: null,
    durationMs: 0,
  };
}

/**
 * Stop where we are and apply the state the intent asked for, without drawing.
 *
 * The answer to a hidden tab, a resize, a density change, a desktop switch, a
 * closed view or a reduced-motion preference arriving mid-run. Every one of
 * those means the same thing: finish the *decision* now, and drop the decoration
 * that was illustrating it. A lifecycle that waited for a final animation frame
 * would be one that never completes in a background tab.
 */
export function cancelMotion(current, reason = 'cancelled') {
  if (!RUNNING.includes(current.state)) return { ...current, reason };
  return {
    state: current.direction === 'collapse' ? MOTION.MINIMIZED : MOTION.IDLE,
    generation: nextGeneration(),
    direction: null,
    collapse: current.direction === 'collapse' ? 1 : 0,
    startedAt: null,
    durationMs: 0,
    reason,
  };
}

/** Whether this window is drawn as a window right now. */
export function isPresented(motion) {
  return motion.state !== MOTION.MINIMIZED;
}

/** Whether the overlay should be painting for this window. */
export function isAnimating(motion) {
  return RUNNING.includes(motion.state);
}
