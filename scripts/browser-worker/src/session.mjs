/**
 * One agent desktop's managed browser session.
 *
 * A session owns a Chromium process and one browser context per desktop. Views
 * are pages inside that context: an app view served through Vela's gateway, or
 * an approved website. The context is what makes two desktops independent —
 * separate cookies, separate storage, separate pointer and focus — and the
 * network policy is what keeps either of them off the rest of the computer.
 *
 * This is application isolation with an enforced network boundary. It is not a
 * claim that a browser context sandboxes hostile code: Chromium's own sandbox
 * stays on and the process stays under Vela's supervision, and anything we
 * cannot enforce is left unavailable instead of approximated.
 */

import { existsSync } from 'node:fs';

import { chromium } from 'playwright-core';
import { checkServerAddress, decide } from './network-policy.mjs';
import { composeObservation, DOM_VERSION_SCRIPT, LIMITS, observePage } from './observe.mjs';
import {
  afterState,
  checkKey,
  checkPoint,
  checkScroll,
  checkText,
  checkTimeout,
  clickElement,
  clickPoint,
  pressKey,
  scrollTarget,
  typeInto,
  waitFor,
} from './input.mjs';
import { Observations, resolveTarget } from './targets.mjs';

/** The agent's screen. Explicit, because an observation records the size it saw. */
export const DEFAULT_VIEWPORT = Object.freeze({ width: 1280, height: 800 });

/**
 * Whether the pinned browser build is actually on this machine.
 *
 * The host asks before accepting executable work, so a missing runtime is an
 * explanation up front rather than a failure halfway through a task.
 */
export function browserAvailability() {
  let executablePath = null;
  try {
    executablePath = chromium.executablePath();
  } catch (error) {
    return { available: false, reason: String(error.message).split('\n')[0], executablePath: null };
  }
  if (!executablePath || !existsSync(executablePath)) {
    return {
      available: false,
      reason: 'the pinned Chromium build is not installed',
      executablePath,
    };
  }
  return { available: true, reason: null, executablePath };
}

export class SessionError extends Error {
  constructor(message, code = 'worker_error') {
    super(message);
    this.code = code;
  }
}

export class DesktopSession {
  /**
   * @param {object} options
   * @param {string} options.desktopId          durable workspace identity
   * @param {string} options.runtimeSessionId   this browser lifetime
   * @param {object} options.policy             from `createPolicy`
   * @param {(event: object) => void} options.onEvent  boundary and view events
   */
  constructor({ desktopId, runtimeSessionId, policy, onEvent = () => {} }) {
    this.desktopId = desktopId;
    this.runtimeSessionId = runtimeSessionId;
    this.controlEpoch = 0;
    this.policy = policy;
    this.onEvent = onEvent;
    this.browser = null;
    this.context = null;
    /** @type {Map<string, import('playwright-core').Page>} */
    this.views = new Map();
    this.denials = [];
    // One observation per view, and the element handles that belong to it.
    this.observations = new Observations();
  }

  async start({ headless = true, executablePath } = {}) {
    this.browser = await chromium.launch({
      headless,
      // Real Chromium, not the headless shell the runtime would otherwise pick.
      // The agent's screen is the same screen a human takes over, so it has to
      // render through the same engine; the shell is a cut-down binary.
      channel: 'chromium',
      ...(executablePath ? { executablePath } : {}),
      // No --no-sandbox. A platform that cannot run the sandbox is reported as
      // unavailable rather than launched with it turned off.
      args: ['--disable-background-networking', '--no-first-run', '--no-default-browser-check'],
    });
    this.context = await this.browser.newContext({
      viewport: { ...DEFAULT_VIEWPORT },
      // Service workers escape route interception, so they are off until a
      // tested policy covers them. Phase 0 proves this rather than assuming it.
      serviceWorkers: 'block',
      acceptDownloads: false,
      javaScriptEnabled: true,
    });
    // Host inspection code, installed before any document runs, in every frame.
    // All it does is count mutations; it is what lets an action notice that the
    // page moved between being looked at and being touched.
    await this.context.addInitScript(DOM_VERSION_SCRIPT);
    await this.context.route('**/*', (route, request) => this.#screen(route, request));
    // WebSocket handshakes do not pass through `route`, so they get their own
    // pass over the same policy. A transport we cannot screen stays disabled.
    await this.context.routeWebSocket('**/*', (ws) => this.#screenSocket(ws));
    return { version: this.browser.version(), viewport: { ...DEFAULT_VIEWPORT } };
  }

  /** Every request in this context passes here, whatever started it. */
  async #screen(route, request) {
    const verdict = decide(request.url(), this.policy);
    if (verdict.allowed) {
      await route.continue();
      return;
    }
    this.#deny({
      stage: 'request',
      resourceType: request.resourceType(),
      url: request.url(),
      reason: verdict.reason,
    });
    await route.abort('blockedbyclient');
  }

  /** The same decision for a WebSocket handshake. */
  #screenSocket(ws) {
    const target = ws.url();
    const verdict = decide(target, this.policy);
    if (verdict.allowed) {
      ws.connectToServer();
      return;
    }
    this.#deny({ stage: 'websocket', resourceType: 'websocket', url: target, reason: verdict.reason });
    ws.close({ code: 1008, reason: 'not allowed on this desktop' });
  }

  #deny(record) {
    const entry = { ...record, at: Date.now() };
    this.denials.push(entry);
    if (this.denials.length > 500) this.denials.shift();
    this.onEvent({ type: 'network_denied', ...entry });
  }

  /** The boundary decisions this session made, for tests and for the host's record. */
  denialLog() {
    return this.denials.slice();
  }

  /**
   * Open a view. `url` is checked before a page is even created, so a denied
   * target never gets a tab it could keep using.
   */
  async openView(viewId, url, bootstrap = null) {
    if (this.views.has(viewId)) {
      // A view id names one window for its whole life, and the address it
      // renders derives from that id. Opening it again means "make sure it is
      // there", so the page that already is gets returned rather than a second
      // one nobody is tracking — and rather than an error for a request that
      // has already been satisfied.
      const open = this.views.get(viewId);
      return { viewId, url: open.url(), title: await open.title(), reused: true };
    }
    const verdict = decide(url, this.policy);
    if (!verdict.allowed) {
      this.#deny({ stage: 'open_view', resourceType: 'document', url, reason: verdict.reason });
      throw new SessionError(`${verdict.reason}`, 'navigation_denied');
    }
    const page = await this.context.newPage();
    this.#guard(page, viewId);
    this.views.set(viewId, page);
    if (bootstrap) {
      // The app host's session goes into the page before it loads, rather than
      // into its address. An address is written down in more places than
      // anybody intends, and a bearer token in one of those is a bearer token
      // in a log.
      await page.addInitScript((value) => {
        window.__velaAgentSession = value;
      }, bootstrap);
    }
    const response = await page.goto(url, { waitUntil: 'domcontentloaded' });
    await this.#checkAddress(page, response);
    return { viewId, url: page.url(), title: await page.title() };
  }

  /**
   * Watch one page for the things route interception does not cover: a popup
   * aiming somewhere else, and a navigation that ended up at a denied URL.
   */
  #guard(page, viewId) {
    page.on('popup', async (popup) => {
      const target = popup.url();
      const verdict = decide(target, this.policy);
      if (!verdict.allowed) {
        this.#deny({ stage: 'popup', resourceType: 'document', url: target, reason: verdict.reason });
        await popup.close().catch(() => {});
      }
    });
    page.on('framenavigated', (frame) => {
      const target = frame.url();
      // A navigation replaces what was observed, so whatever the agent was
      // holding a reference to stops being addressable. Refusing a stale click
      // is only possible because this happens whether or not anybody noticed.
      this.observations.invalidate(viewId, 'the view navigated').catch(() => {});
      if (!target || target === 'about:blank') return;
      const verdict = decide(target, this.policy);
      if (!verdict.allowed) {
        this.#deny({
          stage: frame === page.mainFrame() ? 'navigation' : 'frame_navigation',
          resourceType: 'document',
          url: target,
          reason: verdict.reason,
        });
        page.goto('about:blank').catch(() => {});
      }
    });
  }

  /** Stop a view whose approved hostname turned out to resolve somewhere private. */
  async #checkAddress(page, response) {
    if (!response) return;
    let address = null;
    try {
      address = await response.serverAddr();
    } catch {
      return; // Not every response can report one; the URL check already ran.
    }
    const verdict = checkServerAddress(response.url(), address?.ipAddress, this.policy);
    if (!verdict.allowed) {
      this.#deny({
        stage: 'resolved_address',
        resourceType: 'document',
        url: response.url(),
        reason: verdict.reason,
      });
      await page.goto('about:blank').catch(() => {});
      throw new SessionError('the approved host resolved to a private address', 'network_denied');
    }
  }

  /**
   * A frame of one view, for the viewer and for window motion.
   *
   * Scoped to the view on purpose: a capture of the whole desktop would contain
   * the owner's approval controls, and those are exactly what the agent must
   * never see. The result carries the identities and the size it was taken at,
   * so a frame from a replaced session or a changed viewport can be rejected
   * rather than drawn.
   */
  async captureFrame(viewId) {
    const page = this.page(viewId);
    const viewport = page.viewportSize() || { ...DEFAULT_VIEWPORT };
    const deviceScaleFactor = await page.evaluate(() => window.devicePixelRatio);
    const image = await page.screenshot({ type: 'png' });
    return {
      viewId,
      desktopId: this.desktopId,
      runtimeSessionId: this.runtimeSessionId,
      controlEpoch: this.controlEpoch,
      width: viewport.width,
      height: viewport.height,
      deviceScaleFactor,
      capturedAt: Date.now(),
      image,
    };
  }

  page(viewId) {
    const page = this.views.get(viewId);
    if (!page) throw new SessionError(`no view ${viewId}`, 'unknown_view');
    return page;
  }

  #viewportOf(page) {
    return page.viewportSize() || { ...DEFAULT_VIEWPORT };
  }

  /* ------------------------------------------------ observing and acting -- */

  /**
   * Look at one view.
   *
   * Replaces whatever observation that view had, which is what makes references
   * from an older one stop working. The result separates what the host knows
   * about the view from what the page said about itself, because the second is
   * written by the thing being observed.
   */
  async observe(viewId, { limits = LIMITS } = {}) {
    const page = this.page(viewId);
    const viewport = page.viewportSize() || { ...DEFAULT_VIEWPORT };
    const deviceScaleFactor = await page.evaluate(() => window.devicePixelRatio).catch(() => 1);
    const { frames, handles, notes } = await observePage(page, { limits });
    const observationId = this.observations.nextId();
    const observation = composeObservation(frames, { observationId, viewId, revision: 0 });
    const record = await this.observations.record(viewId, {
      observationId,
      handles,
      controls: observation.page.controls,
      viewport,
      deviceScaleFactor,
      url: observation.page.url,
      domVersion: observation.page.domVersion,
    });
    return {
      ...observation,
      revision: record.revision,
      view: {
        viewId,
        desktopId: this.desktopId,
        runtimeSessionId: this.runtimeSessionId,
        controlEpoch: this.controlEpoch,
        url: page.url(),
        viewport,
        deviceScaleFactor,
        takenAt: record.takenAt,
      },
      notes,
    };
  }

  /**
   * Do one thing to a view, then say what it looks like afterwards.
   *
   * Every action names the observation it was decided from. That observation is
   * required to still be the current one, and the control it points at is
   * re-checked against the description the agent was given, before anything
   * happens. Both checks are here rather than in the caller so there is one
   * place an action can start from.
   */
  async act(viewId, action) {
    const page = this.page(viewId);
    const kind = String(action?.action || '');
    // Waiting touches nothing, so it may happen before the first look — which is
    // when it is most useful. Everything else names the observation it was
    // decided from, because acting without saying what you saw is acting on
    // whatever is there now.
    const record =
      kind === 'wait' && !action?.observationId
        ? { viewId, revision: 0, handles: [], controls: [], viewport: this.#viewportOf(page), url: page.url(), domVersion: null }
        : this.observations.require(viewId, action?.observationId);
    let target = null;
    let acted = { kind };

    try {
      if (kind === 'click') {
        if (action.ref) {
          target = await resolveTarget(record, action.ref, {});
          await clickElement(target.element, {
            button: action.button || 'left',
            clickCount: action.clickCount || 1,
          });
          acted = { kind, ref: action.ref, name: target.described.name, role: target.described.role };
        } else {
          const point = checkPoint(action.point, record.viewport);
          await clickPoint(page, point);
          acted = { kind, point };
        }
      } else if (kind === 'type') {
        target = await resolveTarget(record, action.ref, { expect: { editable: true } });
        const text = checkText(action.text ?? '');
        await typeInto(target.element, text, { mode: action.mode || 'replace' });
        acted = {
          kind,
          ref: action.ref,
          name: target.described.name,
          mode: action.mode || 'replace',
          characters: text.length,
        };
      } else if (kind === 'scroll') {
        const delta = checkScroll(action);
        if (action.ref) {
          target = await resolveTarget(record, action.ref, {});
          await scrollTarget(page, target.element, delta);
          acted = { kind, ref: action.ref, ...delta };
        } else {
          await scrollTarget(page, null, delta);
          acted = { kind, ...delta };
        }
      } else if (kind === 'key') {
        const key = checkKey(action.key);
        await pressKey(page, key);
        acted = { kind, key };
      } else if (kind === 'wait') {
        const result = await waitFor(page, action.condition, checkTimeout(action.timeoutMs));
        acted = { kind, condition: action.condition?.type, ...result };
      } else {
        throw new SessionError(`${kind || 'that'} is not an action`, 'unknown_command');
      }
    } finally {
      if (target) await target.element.dispose().catch(() => {});
    }

    const after = await afterState(page);
    // Anything that touched the page makes the observation it was decided from
    // no longer describe what is there. A waiting tool did not touch anything.
    if (kind !== 'wait') await this.observations.invalidate(viewId, `a ${kind} changed the view`);
    return {
      acted,
      after: {
        ...after,
        changed: after.domVersion !== null && after.domVersion !== record.domVersion,
        navigated: after.url !== record.url,
      },
      observationSpent: kind !== 'wait',
    };
  }

  /** Navigate an open view somewhere else it is allowed to go. */
  async navigateView(viewId, url) {
    const page = this.page(viewId);
    const verdict = decide(url, this.policy);
    if (!verdict.allowed) {
      this.#deny({ stage: 'navigate', resourceType: 'document', url, reason: verdict.reason });
      throw new SessionError(`${verdict.reason}`, 'navigation_denied');
    }
    await this.observations.invalidate(viewId, 'the view was sent somewhere else');
    const response = await page.goto(url, { waitUntil: 'domcontentloaded' });
    await this.#checkAddress(page, response);
    return { viewId, url: page.url(), title: await page.title() };
  }

  async closeView(viewId) {
    const page = this.views.get(viewId);
    if (!page) return false;
    this.views.delete(viewId);
    await this.observations.invalidate(viewId, 'the view was closed');
    await page.close().catch(() => {});
    return true;
  }

  /**
   * A new control generation. Commands issued under the old one stop being
   * valid, and so does everything anybody was looking at: control changing hands
   * is exactly when a held reference is most dangerous.
   */
  bumpControlEpoch() {
    this.controlEpoch += 1;
    for (const viewId of this.views.keys()) {
      this.observations.invalidate(viewId, 'control of this desktop changed hands').catch(() => {});
    }
    return this.controlEpoch;
  }

  async stop() {
    await this.observations.clear();
    this.views.clear();
    if (this.context) await this.context.close().catch(() => {});
    if (this.browser) await this.browser.close().catch(() => {});
    this.context = null;
    this.browser = null;
  }
}
