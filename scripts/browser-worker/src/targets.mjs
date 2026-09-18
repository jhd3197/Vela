/**
 * The bridge between "what the agent saw" and "what it is about to touch".
 *
 * A control reference is only meaningful inside the observation that produced
 * it. That is the point of this module: an observation is held for one view,
 * every reference is checked against it, and the element behind a reference is
 * re-verified against the description the agent was given before anything
 * happens to it.
 *
 * Re-verification is what makes a mutating page safe rather than merely
 * detectable. A held handle cannot become a different element — but the element
 * it points at can be detached, disabled, moved off screen or relabelled from
 * "Save draft" to "Delete everything" between the look and the click. When the
 * thing in front of the agent no longer matches what the agent was told, the
 * action is refused and a fresh observation is the only way forward. Guessing is
 * how an agent clicks the wrong button.
 */

export class TargetError extends Error {
  constructor(message, code = 'stale_observation') {
    super(message);
    this.code = code;
  }
}

const REFERENCE = /^f(\d+):e(\d+)$/;

/**
 * The current observation of every open view.
 *
 * One per view, replaced rather than accumulated: an agent acts on what it can
 * see now, and keeping older observations addressable would mean keeping older
 * pages addressable too.
 */
export class Observations {
  constructor() {
    /** @type {Map<string, object>} */
    this.current = new Map();
    this.counter = 0;
  }

  /** A fresh id. Opaque, sequential, and never reused within a worker lifetime. */
  nextId() {
    this.counter += 1;
    return `o${this.counter}`;
  }

  /**
   * Store an observation for a view, disposing whatever it replaces.
   *
   * `revision` counts everything that has invalidated this view's observations
   * so far, so a caller can tell "you are one behind" from "that was never an
   * observation of this view".
   */
  async record(viewId, { observationId, handles, controls, viewport, deviceScaleFactor, url, domVersion }) {
    const previous = this.current.get(viewId);
    const revision = previous ? previous.revision + 1 : 1;
    if (previous) await disposeAll(previous.handles);
    const record = {
      observationId,
      viewId,
      revision,
      handles,
      controls,
      viewport,
      deviceScaleFactor,
      url,
      domVersion,
      takenAt: Date.now(),
    };
    this.current.set(viewId, record);
    return record;
  }

  /**
   * The observation an action claims to be acting on.
   *
   * An action with no observation id is refused rather than applied to the
   * latest one: "click the Save button" without saying which screen it was on is
   * exactly the ambiguity that ends with the wrong screen being clicked.
   */
  require(viewId, observationId) {
    const record = this.current.get(viewId);
    if (!record) {
      throw new TargetError('observe this view before acting on it', 'stale_observation');
    }
    if (!observationId) {
      throw new TargetError('this action must name the observation it is acting on', 'protocol_error');
    }
    if (record.observationId !== observationId) {
      throw new TargetError(
        `observation ${observationId} has been replaced; observe again`,
        'stale_observation',
      );
    }
    return record;
  }

  /** Drop a view's observation. Called on navigation, resize and control change. */
  async invalidate(viewId, reason = 'the view changed') {
    const record = this.current.get(viewId);
    if (!record) return false;
    this.current.delete(viewId);
    await disposeAll(record.handles);
    record.invalidatedBecause = reason;
    return true;
  }

  /** Drop everything. Used when a session closes. */
  async clear() {
    const records = [...this.current.values()];
    this.current.clear();
    for (const record of records) await disposeAll(record.handles);
  }
}

async function disposeAll(handles) {
  for (const handle of handles || []) {
    await handle.dispose().catch(() => {});
  }
}

/**
 * Turn a reference into a live element, having checked that it is still the
 * element the agent was shown.
 *
 * `expect` is optional and comes from the caller, not the page: a tool that
 * knows it needs an editable field says so, and a reference to a button is
 * refused with a reason instead of typing into nothing.
 */
export async function resolveTarget(record, ref, { expect = {} } = {}) {
  const match = REFERENCE.exec(String(ref || ''));
  if (!match) throw new TargetError(`${ref} is not a control reference`, 'protocol_error');
  const frameIndex = Number(match[1]);
  const elementIndex = Number(match[2]);
  const handle = record.handles[frameIndex];
  if (!handle) throw new TargetError(`that observation had no frame ${frameIndex}`, 'stale_observation');
  const described = record.controls.find((control) => control.ref === ref);
  if (!described) throw new TargetError(`${ref} was not in that observation`, 'stale_observation');

  const elementHandle = await handle.evaluateHandle(
    (elements, index) => elements[index],
    elementIndex,
  );
  const element = elementHandle.asElement();
  if (!element) {
    await elementHandle.dispose().catch(() => {});
    throw new TargetError(`${ref} is no longer on the page`, 'stale_observation');
  }

  const now = await element
    .evaluate((node) => {
      const box = node.getBoundingClientRect();
      const style = window.getComputedStyle(node);
      const name = (
        node.getAttribute('aria-label') ||
        (node.labels && node.labels.length
          ? [...node.labels].map((label) => label.textContent || '').join(' ')
          : '') ||
        node.getAttribute('placeholder') ||
        node.getAttribute('title') ||
        node.getAttribute('alt') ||
        node.getAttribute('name') ||
        node.textContent ||
        ''
      )
        .replace(/\s+/g, ' ')
        .trim();
      return {
        connected: node.isConnected,
        name,
        disabled: Boolean(node.disabled || node.getAttribute('aria-disabled') === 'true'),
        hidden: style.visibility === 'hidden' || style.display === 'none',
        acceptsFiles:
          node.tagName === 'INPUT' &&
          (node.getAttribute('type') || '').toLowerCase() === 'file',
        editable:
          node.isContentEditable ||
          node.tagName === 'TEXTAREA' ||
          (node.tagName === 'INPUT' &&
            !['checkbox', 'radio', 'button', 'submit', 'reset', 'file', 'range'].includes(
              (node.getAttribute('type') || 'text').toLowerCase(),
            )),
        box: { width: Math.round(box.width), height: Math.round(box.height) },
      };
    })
    .catch(() => null);

  const refuse = async (detail, code = 'stale_observation') => {
    await element.dispose().catch(() => {});
    throw new TargetError(detail, code);
  };

  if (!now) await refuse(`${ref} could not be re-checked; observe again`);
  if (!now.connected) await refuse(`${ref} has been removed from the page; observe again`);
  if (now.hidden || now.box.width === 0 || now.box.height === 0) {
    await refuse(`${ref} is no longer visible; observe again`);
  }
  // The label is the whole reason a reference is safe to act on. A control that
  // renamed itself is a different control as far as an agent is concerned.
  if (!sameName(now.name, described.name)) {
    await refuse(
      `${ref} now reads "${clip(now.name)}" rather than "${clip(described.name)}"; observe again`,
    );
  }
  if (now.disabled && expect.enabled !== false) {
    await refuse(`${ref} is disabled`, 'view_not_ready');
  }
  if (expect.editable && !now.editable) {
    await refuse(`${ref} is not a field you can type into`, 'protocol_error');
  }
  if (expect.file && !now.acceptsFiles) {
    await refuse(`${ref} is not a field you can attach a file to`, 'protocol_error');
  }
  return { element, described, state: now, frameIndex };
}

/** Names are compared as the agent would read them: trimmed, collapsed, clipped. */
function sameName(current, described) {
  const left = String(current || '').replace(/\s+/g, ' ').trim();
  const right = String(described || '').replace(/\s+/g, ' ').trim();
  if (left === right) return true;
  // A description was clipped when it was long, so a long name matches by its
  // surviving prefix rather than failing on the ellipsis the host added.
  if (right.endsWith('…')) return left.startsWith(right.slice(0, -1));
  return false;
}

function clip(value) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  return text.length > 60 ? text.slice(0, 60) + '…' : text;
}

/**
 * Put files into a file field.
 *
 * The paths come from Vela, which generated them when it staged the artifacts.
 * Nothing a page or a model said reaches this: an agent names an artifact id,
 * Vela turns that into the file it stored under a name it chose, and the field
 * receives that. There is no way from here to a path somebody supplied.
 */
export async function attachFiles(element, paths) {
  await element.scrollIntoViewIfNeeded({ timeout: 5000 }).catch(() => {});
  await element.setInputFiles(paths, { timeout: 15_000 });
}
