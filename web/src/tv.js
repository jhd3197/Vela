// TV mode: the dashboard inside the Vela app on a television.
//
// A remote has arrows, OK and Back — no pointer and no Tab key. Arrows move
// focus to the nearest control in that direction; OK is Enter, which buttons
// and links already answer. Everything here is inert outside a TV, and the
// choosing is a pure function so it can be tested without a browser.

const FOCUSABLE = [
  'a[href]',
  'button',
  'input:not([type="hidden"])',
  'select',
  'textarea',
  'summary',
  '[tabindex]',
  '[contenteditable="true"]',
].join(',');

const DIRECTIONS = {
  ArrowUp: 'up',
  ArrowDown: 'down',
  ArrowLeft: 'left',
  ArrowRight: 'right',
};

// Inputs where Left and Right move the caret; Up and Down still leave them.
const CARET_INPUTS = new Set(['text', 'search', 'email', 'url', 'tel', 'password', 'number']);

/** Whether the Vela app says this is a television. */
export function isTvForm(win = globalThis.window) {
  try {
    const info = win?.VelaAndroid?.info?.();
    return Boolean(info) && JSON.parse(info).form === 'tv';
  } catch {
    return false;
  }
}

function centre(rect) {
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
}

/**
 * The candidate to move to from `from` in `direction`, or null.
 *
 * A candidate counts only if it lies beyond the current control's edge in that
 * direction. Among those, distance along the direction wins, with sideways
 * distance weighted heavily: moving right should pick the control beside you,
 * not a nearer one two rows down.
 */
export function pickNext(from, candidates, direction) {
  const origin = centre(from);
  let best = null;
  let bestScore = Infinity;
  for (const candidate of candidates) {
    const rect = candidate.rect;
    const point = centre(rect);
    let along;
    let across;
    if (direction === 'right') {
      along = rect.left - from.right;
      across = point.y - origin.y;
      if (point.x <= origin.x) continue;
    } else if (direction === 'left') {
      along = from.left - rect.right;
      across = point.y - origin.y;
      if (point.x >= origin.x) continue;
    } else if (direction === 'down') {
      along = rect.top - from.bottom;
      across = point.x - origin.x;
      if (point.y <= origin.y) continue;
    } else {
      along = from.top - rect.bottom;
      across = point.x - origin.x;
      if (point.y >= origin.y) continue;
    }
    // Overlapping boxes still count, as a step of nothing.
    const score = Math.max(0, along) + 2 * Math.abs(across);
    if (score < bestScore) {
      best = candidate;
      bestScore = score;
    }
  }
  return best;
}

function usable(element) {
  if (element.disabled || element.getAttribute('tabindex') === '-1') return false;
  if (element.closest('[inert], [aria-hidden="true"], [hidden]')) return false;
  const rect = element.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

// An open modal owns the remote, the same way it owns the keyboard.
function scope(document) {
  const modals = document.querySelectorAll('dialog[open], [role="dialog"][aria-modal="true"]');
  return modals.length ? modals[modals.length - 1] : document;
}

function isTextEntry(element) {
  if (!element) return false;
  if (element.isContentEditable || element.tagName === 'TEXTAREA') return true;
  return element.tagName === 'INPUT' && CARET_INPUTS.has(element.type);
}

// Arriving on a text field must not open the on-screen keyboard: it would cover
// half the screen every time focus passed through a search box. The field is
// held with `inputmode="none"` until OK is pressed on it, which is when a
// person with a remote has asked to type.
const HOLD = 'tvHold';

function hold(element) {
  if (!isTextEntry(element) || element.dataset[HOLD] !== undefined) return;
  element.dataset[HOLD] = element.getAttribute('inputmode') ?? '';
  element.setAttribute('inputmode', 'none');
  element.addEventListener('blur', () => release(element), { once: true });
}

function release(element) {
  const previous = element.dataset[HOLD];
  if (previous === undefined) return;
  delete element.dataset[HOLD];
  if (previous) element.setAttribute('inputmode', previous);
  else element.removeAttribute('inputmode');
}

export function isHeld(element) {
  return Boolean(element?.dataset) && element.dataset[HOLD] !== undefined;
}

function keepsArrow(target, direction) {
  if (!target || target === target.ownerDocument?.body) return false;
  // A held field is only passed through, so every arrow leaves it.
  if (isHeld(target)) return false;
  if (target.isContentEditable || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT')
    return true;
  if (target.tagName === 'INPUT') {
    if (target.type === 'range') return direction === 'left' || direction === 'right';
    return CARET_INPUTS.has(target.type) && (direction === 'left' || direction === 'right');
  }
  // Widgets that do their own arrow handling (menus, lists, tabs, sliders).
  return Boolean(
    target.closest(
      '[role="menu"], [role="listbox"], [role="tablist"], [role="slider"], [role="grid"]',
    ),
  );
}

/** Arrow-key focus movement for the whole document. Returns an uninstall function. */
export function installSpatialNavigation(win = window) {
  const document = win.document;
  const onKey = (event) => {
    const active = document.activeElement;
    if (event.key === 'Enter' && isHeld(active)) {
      // OK on a held field: release it and ask the app for the keyboard,
      // which a WebView never opens for focus that page script set.
      event.preventDefault();
      release(active);
      win.VelaAndroid?.showKeyboard?.();
      return;
    }
    const direction = DIRECTIONS[event.key];
    if (!direction || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey)
      return;
    if (keepsArrow(active, direction)) return;
    const candidates = [...scope(document).querySelectorAll(FOCUSABLE)]
      .filter((element) => element !== active && usable(element))
      .map((element) => ({ element, rect: element.getBoundingClientRect() }));
    if (!candidates.length) return;
    const onSomething = active && active !== document.body && usable(active);
    const next = onSomething
      ? pickNext(active.getBoundingClientRect(), candidates, direction)
      : candidates[0];
    if (!next) return;
    event.preventDefault();
    hold(next.element);
    next.element.focus({ preventScroll: true });
    next.element.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  };
  win.addEventListener('keydown', onKey);
  return () => win.removeEventListener('keydown', onKey);
}

/** Turn TV mode on when the Vela app says this is a television. */
export function startTvMode(win = window) {
  if (!isTvForm(win)) return false;
  win.document.documentElement.dataset.form = 'tv';
  installSpatialNavigation(win);
  return true;
}
