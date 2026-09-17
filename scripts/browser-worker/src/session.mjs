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

import { createHash } from 'node:crypto';
import { existsSync, statSync, unlinkSync } from 'node:fs';
import { join } from 'node:path';

import { chromium } from 'playwright-core';
import { checkServerAddress, decide, siteRuleFor } from './network-policy.mjs';
import { describeRequest, needsDecision } from './site-effects.mjs';
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
import { attachFiles, Observations, resolveTarget } from './targets.mjs';

/** The agent's screen. Explicit, because an observation records the size it saw. */
export const DEFAULT_VIEWPORT = Object.freeze({ width: 1280, height: 800 });

/**
 * How long a paused request waits for Vela's answer before it is abandoned.
 *
 * Abandoning means aborting, which means nothing was sent — a certain outcome,
 * not an unknown one. A longer wait would hold a socket open on somebody else's
 * server while a person decided, and that is not a decision to make for them.
 */
const DECISION_TIMEOUT_MS = 20_000;

/** Largest file the browser may finish downloading, matching Vela's own limit. */
const MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024;

/** Most finished downloads one session holds before Vela has collected them. */
const MAX_PENDING_DOWNLOADS = 20;

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
   * @param {(request: object) => Promise<object>} options.onDecide  ask Vela
   *   about a request that would change something on an approved site
   * @param {string|null} options.downloadsDir  the one directory a finished
   *   download may be written to, chosen and owned by Vela
   * @param {object|null} options.storageState  a remembered signed-in session
   */
  constructor({
    desktopId,
    runtimeSessionId,
    policy,
    onEvent = () => {},
    onDecide = null,
    downloadsDir = null,
    storageState = null,
  }) {
    this.desktopId = desktopId;
    this.runtimeSessionId = runtimeSessionId;
    this.controlEpoch = 0;
    this.policy = policy;
    this.onEvent = onEvent;
    this.onDecide = onDecide;
    this.downloadsDir = downloadsDir;
    this.storageStateIn = storageState;
    this.browser = null;
    this.context = null;
    /** @type {Map<string, import('playwright-core').Page>} */
    this.views = new Map();
    this.denials = [];
    // One observation per view, and the element handles that belong to it.
    this.observations = new Observations();
    /**
     * Things that happened *to* a view rather than because of an action: a
     * finished download, a refused submission, a dialog the page opened, a
     * request whose answer never arrived. Drained into the next result the host
     * asks for, so a task learns about them in the same breath as what it did.
     */
    this.notices = [];
    /**
     * Files staged for one file field, set immediately before the click that
     * opens the chooser. Anything else the page asks for is cancelled: a page
     * that can open a file dialog whenever it likes is a page that can read
     * whatever the person clicks next.
     */
    this.pendingFiles = null;
    /**
     * Requests this session allowed and that have not settled yet. Keyed by the
     * browser's own request object, so an answer is matched to the thing it is
     * an answer to rather than to a URL that may repeat.
     */
    this.inFlight = new Map();
    /**
     * Bodies that went out and were never answered.
     *
     * A later attempt at the same body is refused here rather than asked about
     * again: sending something twice when nobody can say whether the first one
     * arrived is the mistake this whole path exists to avoid.
     *
     * **What this cannot cover.** Chromium retransmits a request itself when the
     * connection dies before any response byte arrives, at a layer below route
     * interception — the handler is consulted once and the server can receive
     * two. Measured, not assumed: `tests/agent-web.test.mjs` demonstrates it.
     * Nothing in a browser can prevent that, which is precisely why an
     * unanswered submission is reported as an *unknown outcome* that a person
     * has to check, rather than as a failure that could be retried.
     */
    this.uncertainDigests = new Set();
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
      // On, and bounded. A download is how a task brings a file back, and the
      // alternative - a page that silently fails to save anything - is a task
      // that reports success with nothing behind it. Where it lands, how big it
      // may be and what it is called are all decided here, not by the site.
      acceptDownloads: true,
      javaScriptEnabled: true,
      // A remembered sign-in for this desktop, when the owner asked for one.
      // Never the person's own browser profile: Vela does not read one and has
      // no way to be pointed at one.
      ...(this.storageStateIn ? { storageState: this.storageStateIn } : {}),
    });
    // Host inspection code, installed before any document runs, in every frame.
    // All it does is count mutations; it is what lets an action notice that the
    // page moved between being looked at and being touched.
    await this.context.addInitScript(DOM_VERSION_SCRIPT);
    await this.context.route('**/*', (route, request) => this.#screen(route, request));
    // WebSocket handshakes do not pass through `route`, so they get their own
    // pass over the same policy. A transport we cannot screen stays disabled.
    await this.context.routeWebSocket('**/*', (ws) => this.#screenSocket(ws));
    this.#watch();
    return { version: this.browser.version(), viewport: { ...DEFAULT_VIEWPORT } };
  }

  /** Every request in this context passes here, whatever started it. */
  async #screen(route, request) {
    const verdict = decide(request.url(), this.policy);
    if (!verdict.allowed) {
      this.#deny({
        stage: 'request',
        resourceType: request.resourceType(),
        url: request.url(),
        reason: verdict.reason,
      });
      await route.abort('blockedbyclient');
      return;
    }
    // The gateway is Vela's own surface and already enforces every effect it
    // exposes, inside the transaction that writes. Asking about it here would be
    // a second, weaker check in front of the real one.
    if (!verdict.site || !needsDecision(request)) {
      await route.continue();
      return;
    }
    await this.#screenEffect(route, request, verdict.site);
  }

  /**
   * A request that would change something on an approved site.
   *
   * Paused here, described, and sent to Vela for a decision. Three answers:
   * send it, refuse it, or say that this one needs a person. Nothing is sent
   * while the question is open, so a refusal is a certainty rather than a race.
   */
  async #screenEffect(route, request, site) {
    const described = describeRequest(request, { viewId: this.#viewOf(request) });
    if (described.bodyDigest && this.uncertainDigests.has(described.bodyDigest)) {
      this.#notice({
        type: 'effect_needs_person',
        viewId: described.viewId,
        method: described.method,
        url: described.url,
        origin: site.origin,
        detail:
          'this was already sent once and never answered; sending it again is a decision for a person',
      });
      await route.abort('blockedbyclient');
      return;
    }
    let decision = { decision: 'person', detail: 'Vela did not answer in time.' };
    if (this.onDecide) {
      try {
        decision = await withTimeout(this.onDecide(described), DECISION_TIMEOUT_MS, decision);
      } catch (error) {
        decision = { decision: 'person', detail: safeDetail(error) };
      }
    }

    if (decision.decision !== 'allow') {
      this.#notice({
        type: decision.decision === 'ask' ? 'effect_pending' : 'effect_needs_person',
        viewId: described.viewId,
        method: described.method,
        url: described.url,
        origin: site.origin,
        detail: decision.detail || null,
        requestId: decision.requestId || null,
      });
      // Aborted, so nothing left this computer. That is the point: a question
      // that is still open has caused nothing, and answering it later is the
      // same request being made again rather than one being un-paused.
      await route.abort('blockedbyclient');
      return;
    }

    // Allowed, and sent by the browser itself rather than re-issued here. A
    // worker that rebuilt the request would have to rebuild a body it cannot
    // read - a file upload is held by the browser as a stream - and would send
    // something subtly different from what was described and approved.
    //
    // What that costs is knowing the outcome directly, so the outcome is watched
    // instead: `#watch` below turns "it finished" and "it never answered" into
    // two different notices, which is the distinction that stops one order
    // becoming two.
    this.inFlight.set(request, { described, origin: site.origin });
    await route.continue();
  }

  /**
   * What became of a request that was allowed to go out.
   *
   * Attached to the context once, at start, and consulted only for requests
   * this session explicitly allowed. `finished` is an answer. `failed` is not a
   * failure: the request left this computer and nobody can say what the other
   * end did with it, which is a different and more careful thing to report.
   */
  #watch() {
    const settle = async (request, outcome) => {
      const held = this.inFlight.get(request);
      if (!held) return;
      this.inFlight.delete(request);
      if (outcome === 'finished') {
        const response = await request.response().catch(() => null);
        this.#notice({
          type: 'effect_sent',
          viewId: held.described.viewId,
          method: held.described.method,
          url: held.described.url,
          origin: held.origin,
          status: response ? response.status() : null,
        });
        return;
      }
      if (held.described.bodyDigest) this.uncertainDigests.add(held.described.bodyDigest);
      this.#notice({
        type: 'effect_uncertain',
        viewId: held.described.viewId,
        method: held.described.method,
        url: held.described.url,
        origin: held.origin,
        digest: held.described.bodyDigest || held.described.url,
        detail: 'the connection ended before an answer arrived',
      });
    };
    this.context.on('requestfinished', (request) => settle(request, 'finished'));
    this.context.on('requestfailed', (request) => settle(request, 'failed'));
  }

  /** Which of this session's views a request belongs to, where that is knowable. */
  #viewOf(request) {
    const frame = typeof request.frame === 'function' ? request.frame() : null;
    const page = frame && typeof frame.page === 'function' ? frame.page() : null;
    if (!page) return null;
    for (const [viewId, candidate] of this.views) {
      if (candidate === page) return viewId;
    }
    return null;
  }

  /**
   * Something that happened to a view rather than because of an action.
   *
   * Kept until the host collects it. Bounded, because a page that opened a
   * dialog in a loop would otherwise be a page that filled this process.
   */
  #notice(record) {
    this.notices.push({ ...record, at: Date.now() });
    if (this.notices.length > 100) this.notices.shift();
    this.onEvent({ type: 'view_notice', desktopId: this.desktopId, ...record });
  }

  /** Everything that has happened since the host last asked. */
  takeNotices() {
    const taken = this.notices;
    this.notices = [];
    return taken;
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

    // A dialog blocks the page until something answers it, and an agent cannot
    // see one. Dismissed, and recorded: a confirm nobody answered is why a task
    // would otherwise sit looking at a page that never changes.
    page.on('dialog', async (dialog) => {
      const message = String(dialog.message() || '').replace(/\s+/g, ' ').slice(0, 300);
      this.#notice({
        type: 'dialog',
        viewId,
        kind: dialog.type(),
        message,
      });
      await dialog.dismiss().catch(() => {});
    });

    // A page that opens a file chooser gets one only when Vela staged a file
    // for this exact interaction. Anything else is cancelled: a chooser the
    // agent did not ask for is a page reaching for whatever it can get.
    page.on('filechooser', async (chooser) => {
      const staged = this.pendingFiles;
      this.pendingFiles = null;
      if (!staged || staged.viewId !== viewId) {
        this.#notice({ type: 'file_chooser_cancelled', viewId });
        await chooser.setFiles([]).catch(() => {});
        return;
      }
      await chooser.setFiles(staged.paths).catch(() => {});
    });

    page.on('download', (download) => {
      this.#receive(viewId, download).catch((error) => {
        this.#notice({ type: 'download_failed', viewId, detail: safeDetail(error) });
      });
    });

    // A navigation that never produced a page - a certificate the browser would
    // not accept, a host that stopped answering - is an attention state rather
    // than a silent blank window.
    page.on('pageerror', () => {});
  }

  /**
   * A file the controlled browser finished downloading.
   *
   * Written under a name Vela generated, in the one directory Vela gave this
   * worker, and only if it is within the size limit. Nothing is extracted,
   * nothing is opened, and the name the site suggested is carried as a label
   * rather than used as a path.
   */
  async #receive(viewId, download) {
    if (!this.downloadsDir) {
      this.#notice({ type: 'download_refused', viewId, detail: 'downloads are not available' });
      await download.cancel().catch(() => {});
      return;
    }
    const suggested = String(download.suggestedFilename() || 'download').slice(0, 200);
    const staged = join(this.downloadsDir, `${createHash('sha256')
      .update(`${this.runtimeSessionId}:${viewId}:${suggested}:${Date.now()}:${Math.random()}`)
      .digest('hex')
      .slice(0, 32)}.part`);
    try {
      await download.saveAs(staged);
    } catch (error) {
      // A download that was interrupted has no complete file behind it, and a
      // partial file handed on as a result is worse than no result.
      this.#notice({ type: 'download_failed', viewId, detail: safeDetail(error) });
      await download.delete().catch(() => {});
      return;
    }
    let size = 0;
    try {
      size = statSync(staged).size;
    } catch (error) {
      this.#notice({ type: 'download_failed', viewId, detail: safeDetail(error) });
      return;
    }
    if (size > MAX_DOWNLOAD_BYTES || size === 0) {
      try {
        unlinkSync(staged);
      } catch {
        /* Already gone is the state this wanted. */
      }
      this.#notice({
        type: 'download_refused',
        viewId,
        name: suggested,
        bytes: size,
        detail: size === 0 ? 'the file arrived empty' : 'the file is larger than Vela accepts',
      });
      return;
    }
    if (this.notices.filter((notice) => notice.type === 'download').length >= MAX_PENDING_DOWNLOADS) {
      try {
        unlinkSync(staged);
      } catch {
        /* Already gone is the state this wanted. */
      }
      this.#notice({ type: 'download_refused', viewId, name: suggested, detail: 'too many downloads are waiting to be collected' });
      return;
    }
    this.#notice({
      type: 'download',
      viewId,
      name: suggested,
      path: staged,
      bytes: size,
      url: download.url(),
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

  /**
   * This desktop's signed-in state, for the owner who asked to keep it.
   *
   * Cookies and origin storage, as Chromium has them now. It leaves the worker
   * only because the owner turned remembering on, and Vela is what decides
   * where it goes and when it is erased.
   */
  async storageState() {
    if (!this.context) throw new SessionError('this desktop has no browser session', 'runtime_unavailable');
    return this.context.storageState();
  }

  /** Forget every cookie and every origin's storage, now, without restarting. */
  async clearStorage() {
    if (!this.context) return { cleared: false };
    await this.context.clearCookies();
    await this.context.clearPermissions();
    return { cleared: true };
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
      // Collected here as well as after an action: a task that waits and then
      // looks should learn that its download finished, not find out three steps
      // later because nothing it did happened to drain the list.
      notices: this.takeNotices(),
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
      } else if (kind === 'attach') {
        // The paths came from Vela's artifact store, which generated them. The
        // agent named an artifact id; it has never seen a path and cannot
        // supply one.
        const paths = Array.isArray(action.paths) ? action.paths.map(String).slice(0, 5) : [];
        if (!paths.length) throw new SessionError('nothing was staged to attach', 'protocol_error');
        target = await resolveTarget(record, action.ref, {});
        if (target.state.acceptsFiles) {
          // A real file field. Filled directly, which is what a person dragging
          // a file onto it amounts to.
          await attachFiles(target.element, paths);
          acted = { kind, via: 'field', ref: action.ref, files: paths.length, name: target.described.name };
        } else {
          // A button that opens a chooser. The files are staged for exactly one
          // chooser, the button is clicked, and the handler consumes them. If no
          // chooser appears the staging is dropped rather than left armed for
          // whatever the page asks for next.
          this.pendingFiles = { viewId, paths };
          await clickElement(target.element, {});
          const consumed = await waitFor(
            page,
            { type: 'idle' },
            Math.min(checkTimeout(action.timeoutMs ?? 5000), 10_000),
          ).then(() => this.pendingFiles === null);
          if (!consumed) {
            this.pendingFiles = null;
            throw new SessionError(
              `${action.ref} did not ask for a file`,
              'view_not_ready',
            );
          }
          acted = { kind, via: 'chooser', ref: action.ref, files: paths.length, name: target.described.name };
        }
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
      // What the click set off besides changing the page: a download that
      // finished, a submission that is waiting for an answer, a dialog. A task
      // that had to guess at these would guess wrong.
      notices: this.takeNotices(),
      observationSpent: kind !== 'wait',
    };
  }

  /**
   * A person typing into a view they are looking at.
   *
   * Deliberately not `act`. An agent's action names the observation it was
   * decided from, because an agent decides from a structured reading of a page
   * it cannot see. A person decides from the picture in front of them, and the
   * picture *is* the observation — so this takes a point and a key, and the
   * host is what checked that the picture was recent enough and that this
   * person holds the lease.
   *
   * What it shares with `act` is the bounds: the same key allowlist, the same
   * text limits, the same refusal to reach past the page. Being a person does
   * not make a browser shortcut into a page interaction.
   */
  async humanInput(viewId, input) {
    const page = this.page(viewId);
    const kind = String(input?.kind || '');
    if (kind === 'click') {
      const point = checkPoint(input.point, this.#viewportOf(page));
      await page.mouse.click(point.x, point.y, {
        button: ['left', 'right', 'middle'].includes(input.button) ? input.button : 'left',
        clickCount: input.clickCount === 2 ? 2 : 1,
      });
    } else if (kind === 'move') {
      const point = checkPoint(input.point, this.#viewportOf(page));
      await page.mouse.move(point.x, point.y);
    } else if (kind === 'scroll') {
      const delta = checkScroll(input);
      await page.mouse.wheel(delta.dx, delta.dy);
    } else if (kind === 'key') {
      await pressKey(page, checkKey(input.key));
    } else if (kind === 'text') {
      // Text the person chose, typed in. Never read from anybody's clipboard:
      // Vela has no business in the host operating system's clipboard and does
      // not ask for it.
      const text = checkText(input.text ?? '');
      if (text) await page.keyboard.type(text, { delay: 4 });
    } else {
      throw new SessionError(`${kind || 'that'} is not something a person can send`, 'unknown_command');
    }
    // A person has touched the page, so whatever the agent was holding is no
    // longer a description of it.
    await this.observations.invalidate(viewId, 'a person used this view');
    const after = await afterState(page);
    return { kind, after };
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

/**
 * A promise with a deadline, resolving to a fallback rather than throwing.
 *
 * Used where the absence of an answer has to mean something specific and safe.
 * A rejected promise here would become an exception inside a route handler, and
 * a route handler that throws leaves a request in an undefined state.
 */
async function withTimeout(promise, ms, fallback) {
  let timer = null;
  try {
    return await Promise.race([
      promise,
      new Promise((resolve) => {
        timer = setTimeout(() => resolve(fallback), ms);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/** One line from an error, with no stack and no path in it. */
function safeDetail(error) {
  const text = error && error.message ? String(error.message) : String(error ?? 'unknown error');
  return text.replace(/\s+/g, ' ').slice(0, 300);
}
