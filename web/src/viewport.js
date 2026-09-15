// One host-owned viewport model.
//
// Every value here is CSS pixels in the top-level layout viewport's coordinate
// space, so a rectangle from `getBoundingClientRect()` can be compared with it
// directly. The visible rectangle is where the browser is actually showing that
// layout viewport right now: pinch zoom, a browser toolbar and an on-screen
// keyboard all move or shrink it.
//
// Nothing in this module reads an app frame's document. Focus inside an app is
// only ever observed as "the iframe element in our own document has focus".

// A smaller visible rectangle is not proof of a keyboard. Browser chrome moves
// by roughly a toolbar's height, and pinch zoom can halve the rectangle without
// occluding anything. Only a bottom gap larger than this, while a text entry
// holds focus and the page is not zoomed, is treated as a keyboard.
export const KEYBOARD_MIN_PX = 120;

// Sub-pixel jitter must not wake every consumer.
export const CHANGE_EPSILON_PX = 0.5;

// Browsers report 1 at default scale; allow for rounding before calling it zoom.
export const ZOOM_EPSILON = 0.02;

const NON_TEXT_INPUT = new Set([
  'button',
  'checkbox',
  'color',
  'file',
  'hidden',
  'image',
  'radio',
  'range',
  'reset',
  'submit',
]);

// Whether the focused element in *our* document is something a phone answers
// with a keyboard. A focused app frame counts: its document is protected, so
// the host cannot see the field inside it, and assuming "no keyboard" there
// would leave an app's editor under the keyboard.
export function hasTextEntryFocus(document) {
  let element = document?.activeElement;
  while (element?.shadowRoot?.activeElement) element = element.shadowRoot.activeElement;
  if (!element) return false;
  if (element.tagName === 'IFRAME') return true;
  if (element.isContentEditable) return true;
  if (element.tagName === 'TEXTAREA' || element.tagName === 'SELECT') return true;
  if (element.tagName !== 'INPUT') return false;
  return !NON_TEXT_INPUT.has(String(element.type || 'text').toLowerCase());
}

// Raw geometry, with a layout-only fallback where VisualViewport is missing.
export function readGeometry(window) {
  const layoutWidth = window?.innerWidth || 0;
  const layoutHeight = window?.innerHeight || 0;
  const visual = window?.visualViewport;
  if (!visual) {
    return {
      left: 0,
      top: 0,
      width: layoutWidth,
      height: layoutHeight,
      scale: 1,
      layoutWidth,
      layoutHeight,
      measured: false,
    };
  }
  return {
    left: visual.offsetLeft || 0,
    top: visual.offsetTop || 0,
    width: visual.width || layoutWidth,
    height: visual.height || layoutHeight,
    scale: visual.scale || 1,
    layoutWidth,
    layoutHeight,
    measured: true,
  };
}

// The visible rectangle in layout-viewport coordinates.
export function visibleRect(geometry) {
  return {
    left: geometry.left,
    top: geometry.top,
    right: geometry.left + geometry.width,
    bottom: geometry.top + geometry.height,
    width: geometry.width,
    height: geometry.height,
  };
}

// The part of `box` that is on screen. Both rectangles must already be in the
// same coordinate space; the result is never negative.
export function intersectRect(box, visible) {
  const left = Math.max(box.left, visible.left);
  const top = Math.max(box.top, visible.top);
  const right = Math.min(box.right ?? box.left + box.width, visible.right);
  const bottom = Math.min(box.bottom ?? box.top + box.height, visible.bottom);
  return {
    left,
    top,
    right: Math.max(left, right),
    bottom: Math.max(top, bottom),
    width: Math.max(0, right - left),
    height: Math.max(0, bottom - top),
  };
}

// How much of each edge of `box` is currently off screen, expressed as insets
// inside the box itself. This is what an app frame needs: the host's own
// offsets are in the host's coordinate space and are not the frame's insets.
export function occlusionOf(box, visible) {
  const right = box.right ?? box.left + box.width;
  const bottom = box.bottom ?? box.top + box.height;
  const height = Math.max(0, bottom - box.top);
  const width = Math.max(0, right - box.left);
  return {
    top: Math.min(height, Math.max(0, visible.top - box.top)),
    left: Math.min(width, Math.max(0, visible.left - box.left)),
    right: Math.min(width, Math.max(0, right - visible.right)),
    bottom: Math.min(height, Math.max(0, bottom - visible.bottom)),
  };
}

// Turn raw geometry into the values layout actually consumes.
//
// `keyboardCandidate` is the caller's focus observation. Without it a hidden
// browser toolbar would masquerade as a keyboard, and every pinch zoom would
// reflow the page.
export function describeViewport(geometry, { keyboardCandidate = false } = {}) {
  const zoomed = geometry.scale > 1 + ZOOM_EPSILON;
  // Only the part of the layout viewport the browser has not already given up.
  // If the browser resized the layout for the keyboard this is ~0, so the
  // keyboard is never subtracted twice.
  const bottomGap = Math.max(0, geometry.layoutHeight - geometry.top - geometry.height);
  const keyboardInset =
    !zoomed && keyboardCandidate && bottomGap >= KEYBOARD_MIN_PX ? Math.round(bottomGap) : 0;
  return {
    left: geometry.left,
    top: geometry.top,
    width: geometry.width,
    height: geometry.height,
    layoutWidth: geometry.layoutWidth,
    layoutHeight: geometry.layoutHeight,
    scale: geometry.scale,
    measured: geometry.measured,
    zoomed,
    keyboardInset,
  };
}

const NUMERIC = ['left', 'top', 'width', 'height', 'layoutWidth', 'layoutHeight', 'keyboardInset'];

export function viewportChanged(previous, next) {
  if (!previous) return true;
  if (previous.zoomed !== next.zoomed || previous.measured !== next.measured) return true;
  if (Math.abs(previous.scale - next.scale) > 0.01) return true;
  return NUMERIC.some((key) => Math.abs(previous[key] - next[key]) > CHANGE_EPSILON_PX);
}

// Layout reads these instead of re-rendering for every visual viewport event.
//
// While the reader is pinch-zoomed the published height falls back to the
// layout viewport. Reflowing the document to the zoomed rectangle would fight
// the gesture, shrink the thing being magnified and take away panning; the
// zoomed page is meant to stay the size it was and be moved around.
export function viewportVariables(state) {
  const height = state.zoomed ? state.layoutHeight : state.height;
  const top = state.zoomed ? 0 : state.top;
  return {
    '--vela-visible-height': `${Math.round(height)}px`,
    '--vela-visible-top': `${Math.round(top)}px`,
    '--vela-keyboard-inset': `${state.keyboardInset}px`,
  };
}

// The shared observer. One instance measures; consumers read or subscribe.
//
// Measurements are coalesced to one per animation frame, reads happen together
// before any write, and consumers are notified only when a value they can see
// actually changed.
export function createViewportStore({
  window: win = globalThis,
  document: doc = win?.document,
  root = doc?.documentElement ?? null,
} = {}) {
  const frames = win?.requestAnimationFrame
    ? { request: win.requestAnimationFrame.bind(win), cancel: win.cancelAnimationFrame.bind(win) }
    : { request: (run) => setTimeout(run, 16), cancel: clearTimeout };
  const listeners = new Set();
  let state = describeViewport(readGeometry(win), { keyboardCandidate: hasTextEntryFocus(doc) });
  let scheduled = 0;
  let disposed = false;

  const publish = () => {
    if (!root?.style) return;
    const variables = viewportVariables(state);
    for (const [name, value] of Object.entries(variables)) root.style.setProperty(name, value);
  };

  const measure = () => {
    if (disposed) return false;
    const next = describeViewport(readGeometry(win), {
      keyboardCandidate: hasTextEntryFocus(doc),
    });
    if (!viewportChanged(state, next)) return false;
    state = next;
    publish();
    for (const listener of [...listeners]) listener(state);
    return true;
  };

  const schedule = () => {
    if (disposed || scheduled) return;
    scheduled = frames.request(() => {
      scheduled = 0;
      measure();
    });
  };

  const visual = win?.visualViewport;
  const bindings = [
    [visual, 'resize', schedule],
    [visual, 'scroll', schedule],
    [win, 'resize', schedule],
    [win, 'orientationchange', schedule],
    [doc, 'focusin', schedule],
    [doc, 'focusout', schedule],
  ].filter(([target]) => typeof target?.addEventListener === 'function');
  for (const [target, type, handler] of bindings) target.addEventListener(type, handler);

  publish();

  return {
    getState: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    // Measure now rather than next frame; returns whether anything changed.
    refresh: () => measure(),
    get listenerCount() {
      return listeners.size;
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      if (scheduled) frames.cancel(scheduled);
      scheduled = 0;
      for (const [target, type, handler] of bindings) target.removeEventListener(type, handler);
      listeners.clear();
    },
  };
}

let shared = null;

// The host's single viewport owner. Created on first use so an isolated test
// fixture or a non-browser import never starts listening by accident.
export function sharedViewport() {
  if (!shared && typeof window !== 'undefined') shared = createViewportStore({ window });
  return shared;
}
