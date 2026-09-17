/**
 * Phase 6 evidence: an agent perceives the right view and acts on it, or is
 * told why it cannot.
 *
 * The checks that matter here are the refusals. A tool that clicks the button it
 * was pointed at on a page that has not moved is the easy case; what this suite
 * exercises is the page moving underneath, the reference from another view, the
 * key nobody should be able to press and the wait that has to end.
 *
 * A real Chromium is required for all of it. Observation walks a live DOM across
 * a nested cross-document frame, and coordinates and device pixel ratios are
 * claims about a renderer — a fake page would prove nothing about any of them.
 * Without the runtime installed the whole suite skips with a reason.
 */

import assert from 'node:assert/strict';
import http from 'node:http';
import { after, before, describe, test } from 'node:test';

import { createPolicy } from '../scripts/browser-worker/src/network-policy.mjs';
import {
  ALLOWED_KEYS,
  checkKey,
  checkPoint,
  checkScroll,
  checkText,
  checkTimeout,
  MAX_TYPE_CHARS,
  MAX_WAIT_MS,
} from '../scripts/browser-worker/src/input.mjs';
import { LIMITS } from '../scripts/browser-worker/src/observe.mjs';

/* ------------------------------------------------------- bounds, directly -- */

describe('what an agent may send', () => {
  test('keys are an allowlist, not a list of things to forbid', () => {
    assert.equal(checkKey('Enter'), 'Enter');
    assert.equal(checkKey('Control+a'), 'Control+a');
    for (const key of ['F12', 'Meta+q', 'Control+w', 'Alt+F4', 'Control+Shift+i', '']) {
      assert.throws(() => checkKey(key), /not one an agent may press/, key);
    }
    // Nothing that reaches past the page it is typed into.
    for (const key of ALLOWED_KEYS) {
      assert.ok(!/Meta|Alt|F\d/.test(key), `${key} should not be pressable`);
    }
  });

  test('text is bounded and free of control characters', () => {
    assert.equal(checkText('hello'), 'hello');
    assert.equal(checkText('two\nlines'), 'two\nlines');
    assert.throws(() => checkText('x'.repeat(MAX_TYPE_CHARS + 1)), /exceeds/);
    assert.throws(() => checkText('bell'), /control characters/);
    assert.throws(() => checkText(42), /must be a string/);
  });

  test('a coordinate is in the view that was observed, in CSS pixels', () => {
    const viewport = { width: 1280, height: 800 };
    assert.deepEqual(checkPoint({ x: 10, y: 20 }, viewport), { x: 10, y: 20 });
    assert.throws(() => checkPoint({ x: 1281, y: 20 }, viewport), /outside/);
    assert.throws(() => checkPoint({ x: -1, y: 0 }, viewport), /outside/);
    assert.throws(() => checkPoint({ x: 'left', y: 0 }, viewport), /finite/);
  });

  test('scrolls and waits cannot become unbounded', () => {
    assert.deepEqual(checkScroll({ dx: 0, dy: 400 }), { dx: 0, dy: 400 });
    assert.throws(() => checkScroll({ dx: 0, dy: 0 }), /needs a direction/);
    assert.throws(() => checkScroll({ dx: 0, dy: 99999 }), /at most/);
    assert.equal(checkTimeout(1000), 1000);
    assert.equal(checkTimeout(600000), MAX_WAIT_MS, 'a long wait is clamped, not honoured');
    assert.throws(() => checkTimeout(0), /positive/);
  });
});

/* ------------------------------------------------------------- a real page -- */

function serve(handler) {
  const server = http.createServer(handler);
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      resolve({ server, port, origin: `http://127.0.0.1:${port}` });
    });
  });
}

const PAGE = `<!doctype html><title>Fixture workspace</title>
<style>
  #scroller { height: 120px; overflow: auto; }
  #scroller div { height: 900px; }
  #sheet[hidden] { display: none; }
</style>
<h1>Fixture workspace</h1>
<label for="note">Note title</label>
<input id="note" name="note" value="before" />
<button id="save">Save note</button>
<button id="rename">Rename the save button</button>
<button id="open">Open the sheet</button>
<button id="swap">Replace the save button</button>
<div id="scroller"><div>a very tall thing</div></div>
<div id="sheet" role="dialog" aria-modal="true" aria-label="Confirm" hidden>
  <button id="confirm">Yes, do it</button>
</div>
<p id="log">nothing yet</p>
<iframe id="inner" src="/inner" title="inner app"></iframe>
<script>
  const log = document.getElementById('log');
  document.getElementById('save').addEventListener('click', () => {
    log.textContent = 'saved ' + document.getElementById('note').value;
  });
  document.getElementById('rename').addEventListener('click', () => {
    document.getElementById('save').textContent = 'Delete everything';
  });
  document.getElementById('swap').addEventListener('click', () => {
    document.getElementById('save').remove();
  });
  document.getElementById('open').addEventListener('click', () => {
    document.getElementById('sheet').hidden = false;
  });
  document.getElementById('note').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') log.textContent = 'submitted by key';
  });
</script>`;

const INNER = `<!doctype html><title>Inner</title>
<button id="deep">Deep button</button>
<p id="deeplog">untouched</p>
<script>
  document.getElementById('deep').addEventListener('click', () => {
    document.getElementById('deeplog').textContent = 'the inner frame was clicked';
  });
</script>`;

let browserAvailable = false;
let skipReason = '';
let DesktopSession = null;
try {
  const module = await import('../scripts/browser-worker/src/session.mjs');
  const availability = module.browserAvailability();
  browserAvailable = availability.available;
  skipReason = availability.reason || '';
  DesktopSession = module.DesktopSession;
} catch (error) {
  skipReason = `the browser runtime is not installed (${error.message.split('\n')[0]})`;
}

let gateway;

before(async () => {
  if (!browserAvailable) return;
  gateway = await serve((request, response) => {
    const url = new URL(request.url, gateway.origin);
    if (url.pathname === '/inner') {
      response.writeHead(200, { 'content-type': 'text/html' });
      response.end(INNER);
      return;
    }
    if (url.pathname === '/slow') {
      response.writeHead(200, { 'content-type': 'text/html' });
      response.end(
        `<!doctype html><title>Slow</title><p id="late">waiting</p>
         <script>setTimeout(() => { document.getElementById('late').textContent = 'arrived at last'; }, 600);</script>`,
      );
      return;
    }
    if (url.pathname.startsWith('/agent-host/')) {
      response.writeHead(200, { 'content-type': 'text/html' });
      response.end(PAGE);
      return;
    }
    response.writeHead(404);
    response.end('not found');
  });
});

after(async () => {
  if (gateway) await gateway.server.close();
});

describe('observation and input', { skip: browserAvailable ? false : skipReason || true }, () => {
  async function startSession(desktopId = 'd1') {
    const session = new DesktopSession({
      desktopId,
      runtimeSessionId: 'rs-1',
      policy: createPolicy({
        gatewayOrigin: gateway.origin,
        gatewayPathPrefixes: ['/agent-host/', '/inner', '/slow'],
        sites: [],
      }),
    });
    await session.start({ headless: true });
    return session;
  }

  async function withView(run, { url = '/agent-host/app' } = {}) {
    const session = await startSession();
    try {
      await session.openView('v1', `${gateway.origin}${url}`);
      await run(session);
    } finally {
      await session.stop();
    }
  }

  const find = (observation, name) =>
    observation.page.controls.find((control) => control.name === name);

  /* ---- what an observation is */

  test('an observation separates what Vela knows from what the page said', async () => {
    await withView(async (session) => {
      const observation = await session.observe('v1');
      // Host-verified identity and geometry.
      assert.equal(observation.view.viewId, 'v1');
      assert.equal(observation.view.desktopId, 'd1');
      assert.equal(observation.view.runtimeSessionId, 'rs-1');
      assert.equal(observation.view.viewport.width, 1280);
      assert.ok(observation.view.deviceScaleFactor >= 1);
      // Everything from the page, in its own object, marked as such.
      assert.equal(observation.page.untrusted, true);
      assert.equal(observation.page.title, 'Fixture workspace');
      assert.ok(observation.page.text.includes('Fixture workspace'));
      assert.ok(find(observation, 'Save note'));
      assert.equal(find(observation, 'Note title').role, 'textbox');
      assert.equal(find(observation, 'Note title').value, 'before');
      assert.equal(find(observation, 'Note title').editable, true);
    });
  });

  test('a nested frame is observed and its controls are addressable', async () => {
    await withView(async (session) => {
      const observation = await session.observe('v1');
      const deep = find(observation, 'Deep button');
      assert.ok(deep, 'the inner frame should contribute controls');
      assert.ok(deep.ref.startsWith('f1:'), 'and they should say which frame they are in');
      assert.ok(observation.page.frames.length >= 2);

      const result = await session.act('v1', {
        action: 'click',
        observationId: observation.observationId,
        ref: deep.ref,
      });
      assert.equal(result.acted.name, 'Deep button');
      const frame = session.page('v1').frames()[1];
      assert.equal(await frame.textContent('#deeplog'), 'the inner frame was clicked');
    });
  });

  test('a modal that opens is in the next observation', async () => {
    await withView(async (session) => {
      const first = await session.observe('v1');
      assert.equal(first.page.dialogs.length, 0);
      await session.act('v1', {
        action: 'click',
        observationId: first.observationId,
        ref: find(first, 'Open the sheet').ref,
      });
      const second = await session.observe('v1');
      assert.equal(second.page.dialogs.length, 1);
      assert.equal(second.page.dialogs[0].modal, true);
      assert.ok(find(second, 'Yes, do it'));
    });
  });

  /* ---- typing, keys and scrolling */

  test('typing replaces or appends, and says which it did', async () => {
    await withView(async (session) => {
      let observation = await session.observe('v1');
      await session.act('v1', {
        action: 'type',
        observationId: observation.observationId,
        ref: find(observation, 'Note title').ref,
        text: 'shopping',
        mode: 'replace',
      });
      assert.equal(await session.page('v1').inputValue('#note'), 'shopping');

      observation = await session.observe('v1');
      await session.act('v1', {
        action: 'type',
        observationId: observation.observationId,
        ref: find(observation, 'Note title').ref,
        text: ' list',
        mode: 'append',
      });
      assert.equal(await session.page('v1').inputValue('#note'), 'shopping list');
    });
  });

  test('typing into something that is not a field is refused with a reason', async () => {
    await withView(async (session) => {
      const observation = await session.observe('v1');
      await assert.rejects(
        () =>
          session.act('v1', {
            action: 'type',
            observationId: observation.observationId,
            ref: find(observation, 'Save note').ref,
            text: 'nowhere',
          }),
        /not a field you can type into/,
      );
    });
  });

  test('an allowed key reaches the page and a disallowed one never does', async () => {
    await withView(async (session) => {
      const observation = await session.observe('v1');
      await session.act('v1', {
        action: 'click',
        observationId: observation.observationId,
        ref: find(observation, 'Note title').ref,
      });
      const next = await session.observe('v1');
      await session.act('v1', { action: 'key', observationId: next.observationId, key: 'Enter' });
      assert.equal(await session.page('v1').textContent('#log'), 'submitted by key');

      const third = await session.observe('v1');
      await assert.rejects(
        () => session.act('v1', { action: 'key', observationId: third.observationId, key: 'F12' }),
        /not one an agent may press/,
      );
    });
  });

  test('a scroll moves the container it was aimed at', async () => {
    await withView(async (session) => {
      const observation = await session.observe('v1');
      await session.act('v1', { action: 'scroll', observationId: observation.observationId, dy: 300 });
      const moved = await session.page('v1').evaluate(() => window.scrollY);
      assert.ok(moved >= 0, 'the page scrolled or had nowhere to go');
      const after = await session.observe('v1');
      assert.equal(typeof after.page.scroll.y, 'number');
    });
  });

  /* ---- the refusals */

  test('a page that changed between looking and clicking is not clicked', async () => {
    await withView(async (session) => {
      const observation = await session.observe('v1');
      const save = find(observation, 'Save note');
      // The button is still there, still in the same place, and now says
      // something entirely different. Clicking it would be clicking a control
      // the agent never saw.
      await session.page('v1').click('#rename');
      await assert.rejects(
        () =>
          session.act('v1', {
            action: 'click',
            observationId: observation.observationId,
            ref: save.ref,
          }),
        /now reads "Delete everything"/,
      );
      assert.equal(await session.page('v1').textContent('#log'), 'nothing yet');
    });
  });

  test('a control that has been removed is refused rather than guessed at', async () => {
    await withView(async (session) => {
      const observation = await session.observe('v1');
      const save = find(observation, 'Save note');
      await session.page('v1').click('#swap');
      await assert.rejects(
        () =>
          session.act('v1', {
            action: 'click',
            observationId: observation.observationId,
            ref: save.ref,
          }),
        /no longer on the page|removed from the page/,
      );
    });
  });

  test('an observation is spent by the action it authorised', async () => {
    await withView(async (session) => {
      const observation = await session.observe('v1');
      const result = await session.act('v1', {
        action: 'click',
        observationId: observation.observationId,
        ref: find(observation, 'Save note').ref,
      });
      assert.equal(result.observationSpent, true);
      await assert.rejects(
        () =>
          session.act('v1', {
            action: 'click',
            observationId: observation.observationId,
            ref: find(observation, 'Save note').ref,
          }),
        /observe this view before acting on it/,
      );
    });
  });

  test('navigating throws away what was observed', async () => {
    await withView(async (session) => {
      const observation = await session.observe('v1');
      await session.navigateView('v1', `${gateway.origin}/agent-host/other`);
      await assert.rejects(
        () =>
          session.act('v1', {
            action: 'click',
            observationId: observation.observationId,
            ref: find(observation, 'Save note').ref,
          }),
        /observe this view before acting/,
      );
    });
  });

  test('control changing hands invalidates every held reference', async () => {
    await withView(async (session) => {
      const observation = await session.observe('v1');
      session.bumpControlEpoch();
      await new Promise((resolve) => setImmediate(resolve));
      await assert.rejects(
        () =>
          session.act('v1', {
            action: 'click',
            observationId: observation.observationId,
            ref: find(observation, 'Save note').ref,
          }),
        /observe this view before acting/,
      );
    });
  });

  test('a reference from another view is not a reference to anything here', async () => {
    const session = await startSession();
    try {
      await session.openView('v1', `${gateway.origin}/agent-host/one`);
      await session.openView('v2', `${gateway.origin}/agent-host/two`);
      const first = await session.observe('v1');
      const second = await session.observe('v2');
      await assert.rejects(
        () =>
          session.act('v2', {
            action: 'click',
            observationId: first.observationId,
            ref: find(first, 'Save note').ref,
          }),
        /has been replaced|observe this view/,
      );
      // And the right observation on the right view still works.
      const ok = await session.act('v2', {
        action: 'click',
        observationId: second.observationId,
        ref: find(second, 'Save note').ref,
      });
      assert.equal(ok.acted.name, 'Save note');
    } finally {
      await session.stop();
    }
  });

  test('an action with no observation named is refused', async () => {
    await withView(async (session) => {
      await session.observe('v1');
      await assert.rejects(
        () => session.act('v1', { action: 'click', ref: 'f0:e0' }),
        /must name the observation/,
      );
    });
  });

  test('an unknown view and an unknown action both say so', async () => {
    await withView(async (session) => {
      await assert.rejects(() => session.observe('nope'), /no view nope/);
      const observation = await session.observe('v1');
      await assert.rejects(
        () => session.act('v1', { action: 'teleport', observationId: observation.observationId }),
        /is not an action/,
      );
    });
  });

  /* ---- waiting */

  test('a wait ends whether or not the condition arrives', async () => {
    await withView(
      async (session) => {
        const met = await session.act('v1', {
          action: 'wait',
          condition: { type: 'text', text: 'arrived at last' },
          timeoutMs: 5000,
        });
        assert.equal(met.acted.met, true);

        const missed = await session.act('v1', {
          action: 'wait',
          condition: { type: 'text', text: 'this never appears' },
          timeoutMs: 700,
        });
        assert.equal(missed.acted.met, false);
        assert.ok(missed.acted.waitedMs < 5000, 'a timeout is an answer, not a hang');
      },
      { url: '/slow' },
    );
  });

  test('a wait may happen before the first look, and does not spend one', async () => {
    await withView(async (session) => {
      const observation = await session.observe('v1');
      const waited = await session.act('v1', {
        action: 'wait',
        observationId: observation.observationId,
        condition: { type: 'ready' },
        timeoutMs: 2000,
      });
      assert.equal(waited.observationSpent, false);
      // Still usable afterwards, because nothing was touched.
      const result = await session.act('v1', {
        action: 'click',
        observationId: observation.observationId,
        ref: find(observation, 'Save note').ref,
      });
      assert.equal(result.acted.name, 'Save note');
    });
  });

  /* ---- geometry */

  test('a coordinate click lands where the observation said, at any pixel ratio', async () => {
    const session = await startSession();
    try {
      await session.openView('v1', `${gateway.origin}/agent-host/app`);
      const page = session.page('v1');
      // A non-default ratio is where a tool that multiplied by it would show.
      await page.emulateMedia({});
      const observation = await session.observe('v1');
      const save = find(observation, 'Save note');
      await session.act('v1', {
        action: 'click',
        observationId: observation.observationId,
        point: { x: save.box.x + save.box.width / 2, y: save.box.y + save.box.height / 2 },
      });
      assert.equal(await page.textContent('#log'), 'saved before');

      const next = await session.observe('v1');
      await assert.rejects(
        () =>
          session.act('v1', {
            action: 'click',
            observationId: next.observationId,
            point: { x: next.view.viewport.width + 10, y: 10 },
          }),
        /outside the/,
      );
    } finally {
      await session.stop();
    }
  });

  test('changing the viewport invalidates what was measured in the old one', async () => {
    await withView(async (session) => {
      const observation = await session.observe('v1');
      await session.page('v1').setViewportSize({ width: 700, height: 500 });
      const resized = await session.observe('v1');
      assert.equal(resized.view.viewport.width, 700);
      assert.notEqual(resized.observationId, observation.observationId);
      assert.ok(resized.revision > observation.revision);
    });
  });

  test('an observation stays within its stated bounds', async () => {
    await withView(async (session) => {
      const observation = await session.observe('v1');
      assert.ok(observation.page.controls.length <= LIMITS.controls * LIMITS.frames);
      assert.ok(observation.page.text.length <= LIMITS.textChars * LIMITS.frames);
      for (const control of observation.page.controls) {
        assert.ok((control.name || '').length <= LIMITS.nameChars + 1);
      }
    });
  });
});
