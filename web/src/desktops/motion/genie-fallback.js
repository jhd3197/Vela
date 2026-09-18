// The genie for a window whose pixels cannot be read.
//
// `frame-source.js` explains why most windows have no texture: an app runs in a
// cross-origin frame and a browser will not hand its pixels to the page around
// it. That restriction is the same one that stops any site reading any other,
// and weakening a frame's sandbox to make an animation prettier would be
// trading a real boundary for a decoration.
//
// What is left is the container, which Vela does own. So the window itself —
// the live one, with the app still running inside it — travels to the rail
// icon and shrinks into it, instead of the picture of it doing so. It is a
// plainer motion than the warp: no neck, no swoop, no bands, because those are
// things you do to an image and there is no image. It is the same *event*
// though, over the same duration, into the same icon, and it is honest about
// what it is.
//
// The window is never unmounted to do this. A remount would reload the app,
// end its bridge session and lose whatever was typed into it, which is exactly
// what minimize promises not to do. It is transformed in place.

/** A window never shrinks below this fraction, so it stays a thing in flight. */
export const MIN_SCALE = 0.04;

/** The reference easings, in the form CSS takes them. */
export const COLLAPSE_EASING = 'cubic-bezier(0.55, 0, 1, 0.45)';
export const EXPAND_EASING = 'cubic-bezier(0, 0.55, 0.45, 1)';

const round = (value) => Math.round(value * 1000) / 1000;

/**
 * The transform that lands a window on its rail icon.
 *
 * Both rectangles are in the work area's coordinates, and the transform is
 * written for an element whose origin is its own centre — so the translation is
 * centre to centre and the scale does not drag the window sideways as it
 * shrinks. Returns null when there is nothing sensible to aim at, and the
 * caller falls back to no motion rather than to a guess.
 */
export function collapseTransform(bounds, icon) {
  if (!bounds?.width || !bounds?.height || !icon?.width || !icon?.height) return null;
  const scaleX = Math.max(MIN_SCALE, icon.width / bounds.width);
  const scaleY = Math.max(MIN_SCALE, icon.height / bounds.height);
  const x = Math.round(icon.x + icon.width / 2 - (bounds.x + bounds.width / 2));
  const y = Math.round(icon.y + icon.height / 2 - (bounds.y + bounds.height / 2));
  return `translate(${x}px, ${y}px) scale(${round(scaleX)}, ${round(scaleY)})`;
}

/**
 * What to put on the window this frame.
 *
 * `phase` is which end of the motion is being asked for: `start` is where the
 * transition begins, `end` is where it is going. Two commits rather than one,
 * because a CSS transition needs a value to leave before it has one to arrive
 * at — a window being restored is put at the icon first and released to its own
 * place on the next frame.
 */
export function fallbackStyle({ collapsed, direction, phase, durationMs }) {
  const collapsing = direction === 'collapse';
  const atIcon = collapsing ? phase === 'end' : phase === 'start';
  return {
    transform: atIcon ? collapsed : 'translate(0px, 0px) scale(1, 1)',
    opacity: atIcon ? 0.2 : 1,
    // The first commit is the starting point and must not animate to itself.
    transition:
      phase === 'start'
        ? 'none'
        : `transform ${durationMs}ms ${collapsing ? COLLAPSE_EASING : EXPAND_EASING}, opacity ${durationMs}ms linear`,
  };
}
