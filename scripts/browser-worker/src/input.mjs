/**
 * The only ways an agent can touch a controlled view.
 *
 * Six of them: click, type, scroll, press a key, wait, and nothing else. Each
 * one is bounded, each one is aimed at a target that was just re-verified, and
 * each one reports what the view looked like afterwards so a claim of success
 * can be checked rather than believed.
 *
 * Keys are an allowlist rather than a denylist. A denylist of dangerous
 * combinations is a list somebody has to keep complete forever; an allowlist of
 * the dozen keys a form needs is a list that stays correct when Chromium adds a
 * shortcut. Nothing here reaches the host operating system: these are page-level
 * key events inside a browser Vela started, and the browser has no access to the
 * rest of the computer to begin with.
 */

/** Longest single text entry. Enough for a paragraph, short of a payload. */
export const MAX_TYPE_CHARS = 4000;

/** Longest a `wait` may block, whatever it was asked for. */
export const MAX_WAIT_MS = 15_000;

/** Furthest a single scroll may move, in CSS pixels. */
export const MAX_SCROLL = 4000;

/**
 * Keys an agent may press, and the modifier combinations that go with them.
 *
 * Deliberately no function keys, no Meta/Super, no Alt combinations and no
 * Control+W/N/T: those are browser and window-manager territory, and an agent
 * closing its own view or opening a new window is not an interaction with the
 * page it was asked to work in.
 */
export const ALLOWED_KEYS = Object.freeze(
  new Set([
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
  ]),
);

export class InputError extends Error {
  constructor(message, code = 'protocol_error') {
    super(message);
    this.code = code;
  }
}

export function checkKey(key) {
  const value = String(key || '');
  if (!ALLOWED_KEYS.has(value)) {
    throw new InputError(`${value || 'that key'} is not one an agent may press here`);
  }
  return value;
}

export function checkText(text) {
  if (typeof text !== 'string') throw new InputError('text must be a string');
  if (text.length > MAX_TYPE_CHARS) {
    throw new InputError(`text of ${text.length} characters exceeds the ${MAX_TYPE_CHARS} limit`, 'payload_too_large');
  }
  // Control characters other than a newline or tab are not something a person
  // types, and are how a terminal-shaped surprise arrives.
  if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(text)) {
    throw new InputError('text contains control characters');
  }
  return text;
}

/** A coordinate click is bound to the viewport the observation recorded. */
export function checkPoint(point, viewport) {
  const x = Number(point?.x);
  const y = Number(point?.y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) {
    throw new InputError('a coordinate click needs a finite x and y');
  }
  if (x < 0 || y < 0 || x > viewport.width || y > viewport.height) {
    throw new InputError(
      `(${x}, ${y}) is outside the ${viewport.width}×${viewport.height} view that was observed`,
    );
  }
  // CSS pixels, deliberately. The device pixel ratio belongs to rendering, and
  // an agent that multiplied by it once would click at twice the intended place
  // on a high-density display.
  return { x, y };
}

export function checkScroll(delta) {
  const dx = Number(delta?.dx || 0);
  const dy = Number(delta?.dy || 0);
  if (!Number.isFinite(dx) || !Number.isFinite(dy)) throw new InputError('scroll needs finite deltas');
  if (Math.abs(dx) > MAX_SCROLL || Math.abs(dy) > MAX_SCROLL) {
    throw new InputError(`a single scroll may move at most ${MAX_SCROLL} pixels`);
  }
  if (dx === 0 && dy === 0) throw new InputError('scroll needs a direction');
  return { dx, dy };
}

export function checkTimeout(value) {
  const ms = Number(value ?? 5000);
  if (!Number.isFinite(ms) || ms <= 0) throw new InputError('a wait needs a positive timeout');
  return Math.min(Math.round(ms), MAX_WAIT_MS);
}

/* --------------------------------------------------------------- actions -- */

/** What the view looked like straight after an action, cheaply. */
export async function afterState(page) {
  const state = await page
    .evaluate(() => ({
      url: location.href,
      title: document.title,
      readyState: document.readyState,
      domVersion: Number(window.__velaDomVersion || 0),
      activeRole: document.activeElement ? document.activeElement.tagName.toLowerCase() : null,
    }))
    .catch(() => null);
  return state || { url: page.url(), title: null, readyState: null, domVersion: null, activeRole: null };
}

export async function clickElement(element, { button = 'left', clickCount = 1 } = {}) {
  if (!['left', 'right', 'middle'].includes(button)) throw new InputError(`${button} is not a mouse button`);
  if (![1, 2].includes(Number(clickCount))) throw new InputError('a click is one or two presses');
  await element.scrollIntoViewIfNeeded({ timeout: 5000 });
  await element.click({ button, clickCount: Number(clickCount), timeout: 5000 });
}

export async function clickPoint(page, point) {
  await page.mouse.click(point.x, point.y);
}

/**
 * Put text into a field.
 *
 * `replace` and `append` are separate because they are different intentions and
 * a tool that guessed would eventually guess wrong on a field somebody had
 * already filled in.
 */
export async function typeInto(element, text, { mode = 'replace' } = {}) {
  if (mode !== 'replace' && mode !== 'append') throw new InputError(`${mode} is not replace or append`);
  await element.scrollIntoViewIfNeeded({ timeout: 5000 });
  if (mode === 'replace') {
    await element.fill('', { timeout: 5000 });
  } else {
    await element.focus({ timeout: 5000 });
    // Append means from the end of what is there, not from wherever a caret was
    // left by the last thing that touched this field.
    await element.press('End').catch(() => {});
  }
  // `type` rather than `fill` for the text itself: a field that reacts to each
  // keystroke — a search box, an autocomplete — behaves the way it would for a
  // person, instead of receiving a value it never saw arrive.
  if (text) await element.type(text, { delay: 8, timeout: 10_000 });
}

export async function scrollTarget(page, element, { dx, dy }) {
  if (element) {
    await element.evaluate((node, delta) => node.scrollBy(delta.dx, delta.dy), { dx, dy });
    return;
  }
  await page.mouse.wheel(dx, dy);
}

export async function pressKey(page, key) {
  await page.keyboard.press(key);
}

/**
 * Wait for one explicit condition, bounded.
 *
 * There is no "wait until it works". A wait names what it is waiting for, gets
 * at most fifteen seconds, and answers whether the condition became true — a
 * timeout is an answer, not an error to retry forever.
 */
export async function waitFor(page, condition, timeout) {
  const kind = String(condition?.type || '');
  const started = Date.now();
  try {
    if (kind === 'ready') {
      await page.waitForLoadState('domcontentloaded', { timeout });
    } else if (kind === 'text') {
      const text = String(condition.text || '');
      if (!text || text.length > 200) throw new InputError('wait for text needs a short string');
      await page.waitForFunction(
        (needle) => document.body && document.body.innerText.includes(needle),
        text,
        { timeout, polling: 250 },
      );
    } else if (kind === 'idle') {
      // Two consecutive polls with the same mutation count. "Nothing has
      // changed for a while" is a condition; "the page is finished" is not
      // something a page can be asked.
      await page.evaluate(() => {
        delete window.__velaIdleProbe;
      });
      await page.waitForFunction(
        () => {
          const now = Number(window.__velaDomVersion || 0);
          const previous = window.__velaIdleProbe;
          window.__velaIdleProbe = now;
          return previous !== undefined && previous === now;
        },
        undefined,
        { timeout, polling: 400 },
      );
    } else {
      throw new InputError(`${kind || 'that'} is not a condition this tool waits for`);
    }
    return { met: true, waitedMs: Date.now() - started };
  } catch (error) {
    if (error instanceof InputError) throw error;
    return { met: false, waitedMs: Date.now() - started, reason: 'the condition did not become true in time' };
  }
}
