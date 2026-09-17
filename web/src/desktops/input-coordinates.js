// Turning a click on a picture into a click in a window.
//
// The picture is a frame of a 1280×800 view drawn into whatever box the layout
// gave it, letterboxed to keep its shape. A click lands somewhere in that box,
// and what the engine needs is where that is in the *view* — in CSS pixels,
// because that is the coordinate system the page itself uses.
//
// Device pixel ratio is deliberately not applied. It belongs to rendering: the
// frame is a higher-resolution image of the same 1280×800 view, and multiplying
// by it here would click at twice the intended place on a dense display. The
// frame reports it so a caller can reason about sharpness, and nothing else.
//
// Out of bounds is refused rather than clamped. A click in the letterbox is a
// click on nothing, and snapping it to the nearest edge would be Vela deciding
// the person meant something they did not do.

/** Where a frame of `width`×`height` sits inside `box`, keeping its shape. */
export function fit(box, frame) {
  const width = Number(frame?.width) || 0;
  const height = Number(frame?.height) || 0;
  if (!width || !height || !box?.width || !box?.height) return null;
  const scale = Math.min(box.width / width, box.height / height);
  const drawn = { width: width * scale, height: height * scale };
  return {
    scale,
    left: (box.width - drawn.width) / 2,
    top: (box.height - drawn.height) / 2,
    width: drawn.width,
    height: drawn.height,
  };
}

/**
 * A pointer position inside the displayed box, as a point in the view.
 *
 * Returns null when the pointer is in the letterbox, so the caller sends
 * nothing rather than sending a click at an edge nobody aimed at.
 */
export function toViewPoint({ clientX, clientY }, element, frame) {
  if (!element) return null;
  const box = element.getBoundingClientRect();
  const placed = fit({ width: box.width, height: box.height }, frame);
  if (!placed) return null;
  const x = (clientX - box.left - placed.left) / placed.scale;
  const y = (clientY - box.top - placed.top) / placed.scale;
  if (x < 0 || y < 0 || x > frame.width || y > frame.height) return null;
  // Whole pixels: a page's own hit testing works in them, and a fractional
  // coordinate is precision this does not actually have.
  return { x: Math.round(x), y: Math.round(y) };
}

/** The keys a person may send, matching the engine's list exactly. */
export const SENDABLE_KEYS = new Set([
  'Enter',
  'Tab',
  'Shift+Tab',
  'Escape',
  'Backspace',
  'Delete',
  'ArrowUp',
  'ArrowDown',
  'ArrowLeft',
  'ArrowRight',
  'Home',
  'End',
  'PageUp',
  'PageDown',
  'Space',
  'Control+a',
  'Control+c',
  'Control+v',
  'Control+x',
  'Control+z',
  'Control+y',
]);

/**
 * A keyboard event as the name the engine understands, or null.
 *
 * Null for anything not on the list, including the browser's own shortcuts.
 * Being a person does not turn Ctrl+W into a page interaction, and the refusal
 * happens here as well as at the engine so the key never leaves the tab.
 */
export function toSendableKey(event) {
  if (event.metaKey || event.altKey) return null;
  const key = event.key;
  if (key === ' ' || key === 'Spacebar') return 'Space';
  if (event.ctrlKey) {
    const letter = key.length === 1 ? key.toLowerCase() : '';
    const combo = `Control+${letter}`;
    return SENDABLE_KEYS.has(combo) ? combo : null;
  }
  if (event.shiftKey && key === 'Tab') return 'Shift+Tab';
  return SENDABLE_KEYS.has(key) ? key : null;
}
