/**
 * What one view currently shows, as something an agent can reason about.
 *
 * An observation is deliberately small and deliberately structured. It is not a
 * page dump: it is the view's own identity and geometry, which the host knows
 * and can vouch for, plus a bounded read of what the page says, which it cannot.
 * Those two halves are kept in separate keys — `view` and `page` — because the
 * second is written by whatever the agent is looking at, and an instruction
 * hidden in a button label must never arrive looking like Vela said it.
 *
 * The collector below is host code. It is versioned, it is reviewed, and it is
 * the only script that runs in a controlled page on the agent's behalf. There is
 * no path from a model's words to a script here, which is the whole reason the
 * tool surface has no "evaluate" in it.
 *
 * Element handles are real handles, held by the worker for the lifetime of one
 * observation. Nothing is written into the page to mark elements — a page that
 * can see the marks can move them — and nothing addressable survives the
 * observation it came from.
 */

/** Bumped when the shape below changes, so a stale reader can say so. */
export const OBSERVATION_VERSION = 1;

/** Ceilings. An observation that grows without limit is one nobody can read. */
export const LIMITS = Object.freeze({
  controls: 120,
  textChars: 6000,
  nameChars: 160,
  valueChars: 200,
  frames: 12,
  dialogs: 4,
});

/**
 * The page-side collector.
 *
 * Runs once per frame and returns the elements it chose alongside a plain
 * description of them, so the worker keeps live references without the page ever
 * learning which elements were interesting.
 */
/* c8 ignore start - executes inside the browser, covered by the browser suite */
function collect(limits) {
  const INTERACTIVE = [
    'a[href]',
    'button',
    'input:not([type="hidden"])',
    'select',
    'textarea',
    'summary',
    '[role="button"]',
    '[role="link"]',
    '[role="checkbox"]',
    '[role="radio"]',
    '[role="switch"]',
    '[role="tab"]',
    '[role="menuitem"]',
    '[role="menuitemcheckbox"]',
    '[role="menuitemradio"]',
    '[role="option"]',
    '[role="textbox"]',
    '[role="searchbox"]',
    '[role="combobox"]',
    '[role="slider"]',
    '[contenteditable=""]',
    '[contenteditable="true"]',
  ].join(',');

  const clip = (value, max) => {
    const text = String(value == null ? '' : value)
      .replace(/\s+/g, ' ')
      .trim();
    return text.length > max ? text.slice(0, max) + '…' : text;
  };

  const visible = (element) => {
    const style = window.getComputedStyle(element);
    if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') {
      return false;
    }
    const box = element.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  };

  const roleOf = (element) => {
    const explicit = element.getAttribute('role');
    if (explicit) return clip(explicit, 40);
    const tag = element.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button' || tag === 'summary') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const type = (element.getAttribute('type') || 'text').toLowerCase();
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'submit' || type === 'button' || type === 'reset') return 'button';
      if (type === 'range') return 'slider';
      return 'textbox';
    }
    if (element.isContentEditable) return 'textbox';
    return 'generic';
  };

  /** A usable name, in the order a person would look for one. */
  const nameOf = (element) => {
    const labelled = element.getAttribute('aria-labelledby');
    if (labelled) {
      const parts = labelled
        .split(/\s+/)
        .map((id) => element.ownerDocument.getElementById(id))
        .filter(Boolean)
        .map((node) => node.textContent || '');
      if (parts.length) return clip(parts.join(' '), limits.nameChars);
    }
    const aria = element.getAttribute('aria-label');
    if (aria) return clip(aria, limits.nameChars);
    if (element.labels && element.labels.length) {
      return clip([...element.labels].map((node) => node.textContent || '').join(' '), limits.nameChars);
    }
    for (const attribute of ['placeholder', 'title', 'alt', 'name']) {
      const value = element.getAttribute(attribute);
      if (value) return clip(value, limits.nameChars);
    }
    if (element.tagName === 'INPUT') {
      const type = (element.getAttribute('type') || 'text').toLowerCase();
      if (type === 'submit' || type === 'button' || type === 'reset') {
        return clip(element.value, limits.nameChars);
      }
    }
    return clip(element.textContent, limits.nameChars);
  };

  const valueOf = (element) => {
    if (element.tagName === 'SELECT') return clip(element.value, limits.valueChars);
    if (element.tagName === 'INPUT') {
      const type = (element.getAttribute('type') || 'text').toLowerCase();
      if (type === 'password') return null; // Never read back a secret field.
      if (type === 'checkbox' || type === 'radio') return null;
      return clip(element.value, limits.valueChars);
    }
    if (element.tagName === 'TEXTAREA') return clip(element.value, limits.valueChars);
    if (element.isContentEditable) return clip(element.textContent, limits.valueChars);
    return null;
  };

  const editable = (element) =>
    element.isContentEditable ||
    element.tagName === 'TEXTAREA' ||
    (element.tagName === 'INPUT' &&
      !['checkbox', 'radio', 'button', 'submit', 'reset', 'file', 'range'].includes(
        (element.getAttribute('type') || 'text').toLowerCase(),
      ));

  const elements = [];
  const controls = [];
  const seen = new Set();
  let truncated = false;
  for (const element of document.querySelectorAll(INTERACTIVE)) {
    if (seen.has(element)) continue;
    seen.add(element);
    if (!visible(element)) continue;
    if (controls.length >= limits.controls) {
      truncated = true;
      break;
    }
    const box = element.getBoundingClientRect();
    const index = elements.length;
    elements.push(element);
    controls.push({
      index,
      role: roleOf(element),
      name: nameOf(element),
      value: valueOf(element),
      editable: editable(element),
      disabled: Boolean(element.disabled || element.getAttribute('aria-disabled') === 'true'),
      checked:
        element.getAttribute('aria-checked') ||
        (typeof element.checked === 'boolean' ? String(element.checked) : null),
      expanded: element.getAttribute('aria-expanded'),
      focused: element === document.activeElement,
      box: {
        x: Math.round(box.x),
        y: Math.round(box.y),
        width: Math.round(box.width),
        height: Math.round(box.height),
      },
      inViewport:
        box.bottom > 0 &&
        box.right > 0 &&
        box.top < window.innerHeight &&
        box.left < window.innerWidth,
    });
  }

  // Visible text, walked rather than taken from `innerText`, so a hidden
  // subtree stays out and the budget is spent on what a person would read.
  const pieces = [];
  let used = 0;
  let textTruncated = false;
  const walker = document.createTreeWalker(document.body || document, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const parent = node.parentElement;
    if (!parent) continue;
    const tag = parent.tagName;
    if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT') continue;
    if (!visible(parent)) continue;
    const text = clip(node.nodeValue, 400);
    if (!text) continue;
    if (used + text.length > limits.textChars) {
      textTruncated = true;
      break;
    }
    pieces.push(text);
    used += text.length + 1;
  }

  const dialogs = [];
  for (const node of document.querySelectorAll('dialog[open],[role="dialog"],[role="alertdialog"]')) {
    if (dialogs.length >= limits.dialogs) break;
    if (!visible(node)) continue;
    dialogs.push({
      role: node.getAttribute('role') || 'dialog',
      modal: node.getAttribute('aria-modal') === 'true' || node.tagName === 'DIALOG',
      name: clip(node.getAttribute('aria-label') || node.textContent, limits.nameChars),
    });
  }

  const scroller = document.scrollingElement || document.documentElement;
  return {
    elements,
    report: {
      url: location.href,
      title: clip(document.title, limits.nameChars),
      readyState: document.readyState,
      controls,
      controlsTruncated: truncated,
      text: pieces.join('\n'),
      textTruncated,
      dialogs,
      focus: document.activeElement ? roleOf(document.activeElement) : null,
      scroll: {
        x: Math.round(scroller ? scroller.scrollLeft : 0),
        y: Math.round(scroller ? scroller.scrollTop : 0),
        width: Math.round(scroller ? scroller.scrollWidth : 0),
        height: Math.round(scroller ? scroller.scrollHeight : 0),
      },
      domVersion: Number(window.__velaDomVersion || 0),
    },
  };
}
/* c8 ignore stop */

/**
 * The script the context installs in every document before it runs.
 *
 * All it does is count mutations. That number is reported on an observation and
 * compared when an action is about to use one, so "the page moved under you" is
 * something the host can notice rather than something an agent finds out by
 * clicking the wrong thing.
 */
export const DOM_VERSION_SCRIPT = () => {
  if (window.__velaDomVersion !== undefined) return;
  window.__velaDomVersion = 0;
  const watch = () => {
    const root = document.documentElement;
    if (!root) {
      requestAnimationFrame(watch);
      return;
    }
    new MutationObserver((records) => {
      window.__velaDomVersion += records.length;
    }).observe(root, { subtree: true, childList: true, attributes: true, characterData: true });
  };
  watch();
};

/**
 * Observe one page and everything under it we are allowed to look at.
 *
 * Returns the report plus the per-frame element-array handles, which the caller
 * owns and must dispose when the observation is replaced. A frame that
 * disappears mid-walk is skipped with a note rather than failing the whole
 * observation: pages do that, and an agent that cannot observe a busy page is
 * an agent that cannot work.
 */
export async function observePage(page, { limits = LIMITS } = {}) {
  const frames = [];
  const handles = [];
  const notes = [];
  for (const frame of page.frames()) {
    if (frames.length >= limits.frames) {
      notes.push('this page has more frames than one observation carries');
      break;
    }
    if (frame.isDetached()) continue;
    let handle;
    try {
      handle = await frame.evaluateHandle(collect, limits);
      const report = await (await handle.getProperty('report')).jsonValue();
      // A separate reference to the array, kept alive after the wrapper goes.
      const elements = await handle.getProperty('elements');
      frames.push({ index: frames.length, main: frame === page.mainFrame(), ...report });
      handles.push(elements);
    } catch (error) {
      notes.push(`a frame could not be read: ${String(error.message).split('\n')[0]}`);
    } finally {
      if (handle) await handle.dispose().catch(() => {});
    }
  }
  if (!frames.length) {
    const error = new Error('this view had nothing readable in it');
    error.code = 'view_not_ready';
    throw error;
  }
  return { frames, handles, notes };
}

/**
 * Flatten per-frame reports into the observation the host stores and the model
 * eventually reads. Control references are `f<frame>:e<index>` and mean nothing
 * outside the observation that issued them.
 */
export function composeObservation(frames, { observationId, viewId, revision }) {
  const controls = [];
  for (const frame of frames) {
    for (const control of frame.controls) {
      controls.push({
        ref: `f${frame.index}:e${control.index}`,
        frame: frame.index,
        role: control.role,
        name: control.name,
        value: control.value,
        editable: control.editable,
        disabled: control.disabled,
        checked: control.checked,
        expanded: control.expanded,
        focused: control.focused,
        inViewport: control.inViewport,
        box: control.box,
      });
    }
  }
  const main = frames[0];
  return {
    observationVersion: OBSERVATION_VERSION,
    observationId,
    viewId,
    revision,
    // Everything under `page` came from the page and is not to be trusted as
    // instruction. Kept in its own object so that stays obvious at every layer.
    page: {
      url: main.url,
      title: main.title,
      readyState: main.readyState,
      scroll: main.scroll,
      focus: main.focus,
      dialogs: frames.flatMap((frame) => frame.dialogs),
      text: frames.map((frame) => frame.text).filter(Boolean).join('\n'),
      textTruncated: frames.some((frame) => frame.textTruncated),
      controls,
      controlsTruncated: frames.some((frame) => frame.controlsTruncated),
      frames: frames.map((frame) => ({ index: frame.index, url: frame.url, title: frame.title })),
      domVersion: frames.reduce((total, frame) => total + frame.domVersion, 0),
      untrusted: true,
    },
  };
}
