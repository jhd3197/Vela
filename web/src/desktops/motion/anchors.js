// Where in the work area a window should fly to.
//
// The rail is somewhere else in the document, so its icon has to be measured
// and then translated into the overlay's coordinate space. Measured at the
// moment the motion starts, never remembered: the rail scrolls, its size
// changes with the theme and the viewport, and a coordinate cached from last
// time is how an animation ends up flying into the edge of the screen.
//
// When the icon is not on screen at all — scrolled out of an overflowing rail,
// or on a layout that has no rail — there is no honest target, and the caller
// falls back rather than aiming at a guess. Flying toward a hardcoded
// off-screen point, or toward another desktop's icon, is worse than not flying.

/** The smallest a target may be before it is not worth aiming at. */
const MIN_TARGET = 8;

/**
 * The rectangle of one view's rail entry, in the work area's coordinates.
 *
 * `host` is the element the overlay is drawn inside. Both rectangles come from
 * the same `getBoundingClientRect` space, so subtracting one from the other is
 * the whole conversion.
 */
export function anchorFor(viewId, host, doc = typeof document === 'undefined' ? null : document) {
  if (!viewId || !host || !doc) return null;
  const element = doc.querySelector(`[data-motion-anchor="${CSS.escape(viewId)}"]`);
  if (!element) return null;
  const icon = element.getBoundingClientRect();
  if (icon.width < MIN_TARGET || icon.height < MIN_TARGET) return null;
  const frame = host.getBoundingClientRect();
  const rect = {
    x: Math.round(icon.left - frame.left),
    y: Math.round(icon.top - frame.top),
    width: Math.round(icon.width),
    height: Math.round(icon.height),
  };
  // An icon scrolled out of an overflowing rail is not visible, and a window
  // that flew to where it would have been would fly off the screen.
  const visible =
    icon.bottom > frame.top - icon.height &&
    icon.top < frame.bottom + icon.height &&
    icon.right > 0 &&
    icon.left < (typeof window === 'undefined' ? Infinity : window.innerWidth);
  return visible ? rect : null;
}
