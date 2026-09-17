/**
 * Phase 11 evidence: websites, files and the things that happen around them.
 *
 * All of this is a claim about a real browser, so it is tested in one. A mocked
 * route handler would prove that the code calls itself; what needs proving is
 * that a form post is actually held before it leaves, that an aborted one never
 * reached the server, that a download lands where Vela said and nowhere else,
 * and that two desktops looking at the same site are not signed in as each
 * other.
 *
 * The fixture site is on loopback, which the network policy normally refuses.
 * `allowPrivateSites` is the narrow test seam for that: it lets an *approved*
 * origin be on this machine and changes nothing else, and the first test here
 * is that without it the same origin is still refused.
 *
 * Without the browser runtime installed the whole suite skips with a reason.
 * Install it with `python scripts/setup-browser-worker.py`.
 */

import assert from 'node:assert/strict';
import http from 'node:http';
import { mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, describe, test } from 'node:test';

import { createPolicy, decide, siteRuleFor } from '../scripts/browser-worker/src/network-policy.mjs';
import { fieldNames, needsDecision } from '../scripts/browser-worker/src/site-effects.mjs';

/* ------------------------------------------------ describing a request -- */

describe('describing a request', () => {
  test('field names come out of a body and values never do', () => {
    const form = Buffer.from('user=ada&password=hunter2&note=hello');
    assert.deepEqual(fieldNames('application/x-www-form-urlencoded', form), [
      'user',
      'password',
      'note',
    ]);
    const described = JSON.stringify(fieldNames('application/x-www-form-urlencoded', form));
    assert.ok(!described.includes('hunter2'), 'a value must never reach a description');
  });

  test('a multipart body gives up its part names and nothing else', () => {
    const body = Buffer.from(
      '--x\r\nContent-Disposition: form-data; name="title"\r\n\r\nSecret title\r\n' +
        '--x\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\n\r\nbytes\r\n--x--',
    );
    const names = fieldNames('multipart/form-data; boundary=x', body);
    assert.ok(names.includes('title'));
    assert.ok(names.includes('file'));
    assert.ok(!JSON.stringify(names).includes('Secret title'));
  });

  test('a shape nothing understands is described as nothing, not as a guess', () => {
    assert.deepEqual(fieldNames('application/x-protobuf', Buffer.from([1, 2, 3])), []);
    assert.deepEqual(fieldNames('application/json', Buffer.from('not json')), []);
  });

  test('only an unsafe method needs deciding about', () => {
    assert.equal(needsDecision({ method: () => 'GET' }), false);
    assert.equal(needsDecision({ method: () => 'HEAD' }), false);
    assert.equal(needsDecision({ method: () => 'POST' }), true);
    assert.equal(needsDecision({ method: () => 'delete' }), true);
  });
});

/* ------------------------------------------------------ the policy seam -- */

describe('approved sites on this machine', () => {
  const rule = { origin: 'http://127.0.0.1:8123', effects: 'ask' };

  test('an approved loopback origin is still refused by default', () => {
    const policy = createPolicy({ sites: [rule] });
    const verdict = decide('http://127.0.0.1:8123/orders', policy);
    assert.equal(verdict.allowed, false);
    assert.equal(verdict.reason, 'private_network_denied');
  });

  test('the test seam widens nothing but the origins already approved', () => {
    const policy = createPolicy({ sites: [rule], allowPrivateSites: true });
    assert.equal(decide('http://127.0.0.1:8123/orders', policy).allowed, true);
    // Everything else on this machine and this network is exactly as refused.
    assert.equal(decide('http://127.0.0.1:9999/', policy).allowed, false);
    assert.equal(decide('http://192.168.1.4/', policy).allowed, false);
    assert.equal(decide('http://169.254.169.254/latest/meta-data', policy).allowed, false);
  });

  test('the matched rule travels with the verdict so one lookup answers both questions', () => {
    const policy = createPolicy({ sites: [rule], allowPrivateSites: true });
    assert.equal(decide('http://127.0.0.1:8123/orders', policy).site.effects, 'ask');
    assert.equal(siteRuleFor('http://127.0.0.1:8123/anything', policy).effects, 'ask');
    assert.equal(siteRuleFor('http://127.0.0.1:9999/', policy), null);
  });
});

/* --------------------------------------------------------- the fixtures -- */

function serve(handler) {
  const server = http.createServer(handler);
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      resolve({ server, port, origin: `http://127.0.0.1:${port}` });
    });
  });
}

function close(fixture) {
  return new Promise((resolve) => fixture.server.close(resolve));
}

/**
 * Drain notices until one of a kind turns up, or the deadline passes.
 *
 * Some of these are reported when the browser settles a request rather than
 * when an action returns, so a test that looked exactly once would be a test
 * that passes on an idle machine and fails on a busy one.
 */
async function collectNotices(session, kind, timeoutMs = 5000) {
  const seen = [];
  const deadline = Date.now() + timeoutMs;
  do {
    seen.push(...session.takeNotices());
    if (seen.some((notice) => notice.type === kind)) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  } while (Date.now() < deadline);
  return seen;
}

function readBody(request) {
  return new Promise((resolve) => {
    const chunks = [];
    request.on('data', (chunk) => chunks.push(chunk));
    request.on('end', () => resolve(Buffer.concat(chunks)));
  });
}

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

let site;
let elsewhere;
let workspace;
let downloadsDir;

/** Everything the fixture site received, so "it never arrived" is checkable. */
let received = [];

before(async () => {
  if (!browserAvailable) return;
  workspace = mkdtempSync(join(tmpdir(), 'vela-agent-web-'));
  downloadsDir = join(workspace, 'downloads');

  elsewhere = await serve((request, response) => {
    response.writeHead(200, { 'content-type': 'text/plain' });
    response.end('somewhere nobody approved');
  });

  site = await serve(async (request, response) => {
    const url = new URL(request.url, site.origin);
    const body = request.method === 'GET' ? Buffer.alloc(0) : await readBody(request);
    if (request.method !== 'GET') {
      received.push({ path: url.pathname, method: request.method, body: body.toString('utf8') });
    }

    if (url.pathname === '/') {
      response.writeHead(200, {
        'content-type': 'text/html',
        'set-cookie': `visitor=${url.searchParams.get('who') || 'nobody'}; Path=/`,
      });
      response.end(`<!doctype html><title>Fixture site</title>
        <h1>Fixture site</h1>
        <p id="who">${request.headers.cookie || 'no cookie'}</p>
        <form method="post" action="/orders">
          <input name="item" aria-label="Item" value="widget">
          <button type="submit">Place the order</button>
        </form>
        <form method="post" action="/upload" enctype="multipart/form-data">
          <input type="file" name="attachment" aria-label="Attachment">
          <button type="submit">Send the file</button>
        </form>
        <a id="get-report" href="/report.csv" download="report.csv">Download the report</a>
        <a id="get-huge" href="/huge.bin" download="huge.bin">Download something large</a>
        <button id="ask" onclick="confirm('Are you sure?')">Ask something</button>`);
      return;
    }
    if (url.pathname === '/orders') {
      response.writeHead(200, { 'content-type': 'text/html' });
      response.end('<!doctype html><title>Ordered</title><h1>Ordered</h1>');
      return;
    }
    if (url.pathname === '/upload') {
      response.writeHead(200, { 'content-type': 'text/html' });
      response.end('<!doctype html><title>Uploaded</title><h1>Uploaded</h1>');
      return;
    }
    if (url.pathname === '/away') {
      // A submission whose answer never arrives: the request is received in
      // full and the connection then goes away without one.
      request.socket.destroy();
      return;
    }
    if (url.pathname === '/offsite') {
      response.writeHead(302, { location: `${elsewhere.origin}/secret` });
      response.end();
      return;
    }
    if (url.pathname === '/report.csv') {
      response.writeHead(200, {
        'content-type': 'text/csv',
        'content-disposition': 'attachment; filename="../../report.csv"',
      });
      response.end('item,count\nwidget,3\n');
      return;
    }
    if (url.pathname === '/huge.bin') {
      response.writeHead(200, {
        'content-type': 'application/octet-stream',
        'content-disposition': 'attachment; filename="huge.bin"',
      });
      // Comfortably past the worker's own limit, sent in blocks so the test
      // does not hold a hundred megabytes in memory at once.
      const block = Buffer.alloc(4 * 1024 * 1024, 0x61);
      for (let sent = 0; sent < 104 * 1024 * 1024; sent += block.length) response.write(block);
      response.end();
      return;
    }
    response.writeHead(404);
    response.end('not found');
  });
});

after(async () => {
  if (site) await close(site);
  if (elsewhere) await close(elsewhere);
  if (workspace) rmSync(workspace, { recursive: true, force: true });
});

describe('websites, files and sessions', { skip: browserAvailable ? false : skipReason || true }, () => {
  /**
   * A session against the fixture site.
   *
   * `answer` is Vela: the worker describes a request and this decides. Every
   * description it was given is kept, because what the worker sends is as much
   * the contract as what it does afterwards.
   */
  async function startSession(desktopId, { effects = 'read', answer = null, storageState = null } = {}) {
    const asked = [];
    const session = new DesktopSession({
      desktopId,
      runtimeSessionId: `rs-${desktopId}`,
      downloadsDir,
      storageState,
      policy: createPolicy({
        sites: [{ origin: site.origin, effects }],
        allowPrivateSites: true,
      }),
      onDecide: async (request) => {
        asked.push(request);
        return answer ? answer(request) : { decision: 'person', detail: 'not decided' };
      },
    });
    await session.start({ headless: true });
    session.asked = asked;
    return session;
  }

  test('reading a site is never asked about', async () => {
    const session = await startSession('d1');
    try {
      const view = await session.openView('v1', `${site.origin}/`);
      assert.equal(view.title, 'Fixture site');
      assert.equal(session.asked.length, 0, 'a GET is not something to ask about');
    } finally {
      await session.stop();
    }
  });

  test('a submission to a read-only site never leaves this computer', async () => {
    received = [];
    const session = await startSession('d1', { effects: 'read' });
    try {
      await session.openView('v1', `${site.origin}/`);
      const page = session.page('v1');
      await page.click('button[type=submit]');
      await page.waitForTimeout(400);

      assert.equal(received.length, 0, 'the fixture site must not have received anything');
      const notices = await collectNotices(session, 'effect_needs_person');
      const refused = notices.find((notice) => notice.type === 'effect_needs_person');
      assert.ok(refused, `expected a handed-back notice, saw ${JSON.stringify(notices)}`);
      assert.equal(refused.method, 'POST');
      assert.match(refused.url, /\/orders$/);
    } finally {
      await session.stop();
    }
  });

  test('a submission Vela is deciding about describes its shape and not its values', async () => {
    received = [];
    const session = await startSession('d1', {
      effects: 'ask',
      answer: () => ({ decision: 'ask', requestId: 'req-1' }),
    });
    try {
      await session.openView('v1', `${site.origin}/`);
      const page = session.page('v1');
      await page.fill('input[name=item]', 'a secret thing');
      await page.click('button[type=submit]');
      await page.waitForTimeout(400);

      assert.equal(received.length, 0, 'nothing is sent while a question is open');
      const [described] = session.asked;
      assert.equal(described.method, 'POST');
      assert.deepEqual(described.fields, ['item']);
      assert.ok(described.bodyDigest.length === 64, 'the body travels as a digest');
      assert.ok(
        !JSON.stringify(described).includes('a secret thing'),
        'a value must never reach Vela, because it ends up in a prompt somebody reads',
      );
      const pending = (await collectNotices(session, 'effect_pending')).find(
        (notice) => notice.type === 'effect_pending',
      );
      assert.equal(pending.requestId, 'req-1');
    } finally {
      await session.stop();
    }
  });

  test('an allowed submission is sent once and reports that it was', async () => {
    received = [];
    const session = await startSession('d1', {
      effects: 'ask',
      answer: () => ({ decision: 'allow' }),
    });
    try {
      await session.openView('v1', `${site.origin}/`);
      const page = session.page('v1');
      await page.click('button[type=submit]');
      await page.waitForURL(/\/orders$/, { timeout: 10_000 });

      assert.equal(received.length, 1, 'once, not twice');
      assert.equal(received[0].path, '/orders');
      assert.match(received[0].body, /item=widget/);
      const notices = await collectNotices(session, 'effect_sent');
      const sent = notices.find((notice) => notice.type === 'effect_sent');
      assert.ok(sent, `expected an outcome, saw ${JSON.stringify(notices.map((n) => n.type))}`);
      assert.equal(sent.status, 200);
    } finally {
      await session.stop();
    }
  });

  test('a submission whose answer never arrives is uncertain, not failed', async () => {
    received = [];
    const session = await startSession('d1', {
      effects: 'ask',
      answer: () => ({ decision: 'allow' }),
    });
    try {
      await session.openView('v1', `${site.origin}/`);
      const page = session.page('v1');
      await page.evaluate(() => {
        const form = document.querySelector('form');
        form.action = '/away';
        form.submit();
      });
      await page.waitForTimeout(1500);

      // Vela allowed it once. The fixture may have received it more than once,
      // because Chromium retransmits a request whose connection died before any
      // response arrived and does so below route interception — this asserts
      // that measured behaviour rather than a guarantee nobody can make.
      assert.equal(session.asked.length, 1, 'Vela was asked about it once');
      assert.ok(received.length >= 1, 'the fixture site did receive it');

      const notices = await collectNotices(session, 'effect_uncertain');
      const uncertain = notices.find((notice) => notice.type === 'effect_uncertain');
      assert.ok(uncertain, 'a lost answer is recorded rather than treated as a failure');
      assert.match(uncertain.url, /\/away$/);
      assert.equal(uncertain.digest.length, 64);
      assert.ok(
        session.uncertainDigests.has(uncertain.digest),
        'and the body is remembered, so nothing sends it again through this door',
      );

      // The door this *can* close: the page, or the agent, trying the same body
      // again. That is refused without even being asked about.
      const before = received.length;
      const page2 = session.page('v1');
      await page2.goto(`${site.origin}/`);
      await page2.evaluate(() => {
        const form = document.querySelector('form');
        form.action = '/away';
        form.submit();
      });
      await page2.waitForTimeout(800);
      assert.equal(session.asked.length, 1, 'no second question about the same body');
      assert.equal(received.length, before, 'and nothing more reached the site');
      assert.ok(
        (await collectNotices(session, 'effect_needs_person')).some(
          (notice) =>
            notice.type === 'effect_needs_person' && /already sent once/.test(notice.detail || ''),
        ),
        'trying again is a decision for a person',
      );
    } finally {
      await session.stop();
    }
  });

  test('a redirect out of the approved site is reviewed like any other hop', async () => {
    const session = await startSession('d1', {
      effects: 'ask',
      answer: () => ({ decision: 'allow' }),
    });
    try {
      await session.openView('v1', `${site.origin}/`);
      const page = session.page('v1');
      await page.evaluate(() => {
        const form = document.querySelector('form');
        form.action = '/offsite';
        form.submit();
      });
      await page.waitForTimeout(800);
      assert.ok(
        !page.url().startsWith(elsewhere.origin),
        `the view followed a redirect off the approved site: ${page.url()}`,
      );
      const reasons = session.denialLog().map((entry) => entry.reason);
      assert.ok(
        reasons.includes('private_network_denied') || reasons.includes('site_not_approved'),
        `expected the hop to be refused, saw ${JSON.stringify(reasons)}`,
      );
    } finally {
      await session.stop();
    }
  });

  test('a download lands under a name Vela generated, in the directory Vela owns', async () => {
    const session = await startSession('d1');
    try {
      await session.openView('v1', `${site.origin}/`);
      const page = session.page('v1');
      await page.click('#get-report');
      await page.waitForTimeout(1200);

      const download = (await collectNotices(session, 'download')).find(
        (notice) => notice.type === 'download',
      );
      assert.ok(download, 'a finished download is reported');
      // The site suggested `../../report.csv`. What it gets is a generated name
      // inside the one directory the worker was given.
      assert.ok(download.path.startsWith(downloadsDir), download.path);
      assert.ok(!download.path.includes('..'), download.path);
      // The name the site suggested travels as a label and carries no path,
      // because Vela's own record of it is what a person will read.
      assert.ok(!download.name.includes('/'), download.name);
      assert.ok(!download.name.includes('\\'), download.name);
      assert.match(download.name, /report\.csv$/);
      assert.equal(readFileSync(download.path, 'utf8'), 'item,count\nwidget,3\n');
      assert.equal(download.bytes, 20);
    } finally {
      await session.stop();
    }
  });

  test('a download past the limit is refused and leaves nothing behind', async () => {
    const session = await startSession('d1');
    try {
      await session.openView('v1', `${site.origin}/`);
      const page = session.page('v1');
      const before = readdirSync(downloadsDir).length;
      await page.click('#get-huge');
      await page.waitForTimeout(8000);

      const notices = await collectNotices(session, 'download_refused', 15_000);
      const refused = notices.find((notice) => notice.type === 'download_refused');
      assert.ok(refused, `expected a refusal, saw ${JSON.stringify(notices.map((n) => n.type))}`);
      assert.equal(readdirSync(downloadsDir).length, before, 'nothing oversized is left staged');
    } finally {
      await session.stop();
    }
  });

  test('a file reaches a page only by the path Vela chose', async () => {
    received = [];
    const staged = join(workspace, 'chosen.txt');
    writeFileSync(staged, 'the owner picked this');
    const session = await startSession('d1', {
      effects: 'ask',
      answer: () => ({ decision: 'allow' }),
    });
    try {
      await session.openView('v1', `${site.origin}/`);
      const observation = await session.observe('v1');
      const field = observation.page.controls.find((control) => control.role === 'file');
      assert.ok(field, 'a file field is named as one rather than called a textbox');

      const result = await session.act('v1', {
        action: 'attach',
        observationId: observation.observationId,
        ref: field.ref,
        paths: [staged],
      });
      assert.equal(result.acted.via, 'field');
      assert.equal(result.acted.files, 1);

      const after = await session.observe('v1');
      const send = after.page.controls.find((control) => control.name === 'Send the file');
      await session.act('v1', {
        action: 'click',
        observationId: after.observationId,
        ref: send.ref,
      });
      await session.page('v1').waitForURL(/\/upload$/, { timeout: 10_000 });
      assert.equal(received.length, 1);
      assert.match(received[0].body, /filename="chosen.txt"/);
      assert.match(
        received[0].body,
        /the owner picked this/,
        'the file has to arrive whole: a worker that rebuilt the request would send an empty one',
      );

      // And what Vela was asked about names the field and never its contents.
      const described = session.asked.at(-1);
      assert.match(described.contentType, /multipart\/form-data/);
      assert.ok(described.fields.includes('attachment'));
      assert.ok(
        !JSON.stringify(described).includes('the owner picked this'),
        'the contents of a file are not something to put in a prompt',
      );
    } finally {
      await session.stop();
    }
  });

  test('attaching refuses a reference that is not something files go into', async () => {
    const staged = join(workspace, 'chosen.txt');
    const session = await startSession('d1');
    try {
      await session.openView('v1', `${site.origin}/`);
      const observation = await session.observe('v1');
      const button = observation.page.controls.find((control) => control.name === 'Place the order');
      await assert.rejects(
        () =>
          session.act('v1', {
            action: 'attach',
            observationId: observation.observationId,
            ref: button.ref,
            paths: [staged],
            timeoutMs: 1500,
          }),
        /did not ask for a file/,
      );
      assert.equal(session.pendingFiles, null, 'a chooser that never came leaves nothing armed');
    } finally {
      await session.stop();
    }
  });

  test('a dialog the page opens is dismissed and recorded rather than left blocking', async () => {
    const session = await startSession('d1');
    try {
      await session.openView('v1', `${site.origin}/`);
      const page = session.page('v1');
      await page.click('#ask');
      await page.waitForTimeout(500);
      const dialog = (await collectNotices(session, 'dialog')).find(
        (notice) => notice.type === 'dialog',
      );
      assert.ok(dialog, 'a confirm nobody answers is why a task would sit forever');
      assert.equal(dialog.kind, 'confirm');
      assert.match(dialog.message, /Are you sure/);
      // And the page is still usable afterwards.
      assert.equal(await page.title(), 'Fixture site');
    } finally {
      await session.stop();
    }
  });

  test('two desktops looking at one site are not signed in as each other', async () => {
    const first = await startSession('d1');
    const second = await startSession('d2');
    try {
      await first.openView('v1', `${site.origin}/?who=ada`);
      await second.openView('v1', `${site.origin}/?who=grace`);
      await first.page('v1').reload();
      await second.page('v1').reload();
      assert.match(await first.page('v1').textContent('#who'), /visitor=ada/);
      assert.match(await second.page('v1').textContent('#who'), /visitor=grace/);
    } finally {
      await first.stop();
      await second.stop();
    }
  });

  test('a kept sign-in is what the next browser starts with, and only if it was kept', async () => {
    const first = await startSession('d1');
    let state;
    try {
      await first.openView('v1', `${site.origin}/?who=ada`);
      state = await first.storageState();
      assert.ok(state.cookies.some((cookie) => cookie.value === 'ada'));
    } finally {
      await first.stop();
    }

    // Without it: a fresh browser, signed in as nobody.
    const forgotten = await startSession('d1');
    try {
      await forgotten.openView('v1', `${site.origin}/nothing`);
      await forgotten.page('v1').goto(`${site.origin}/`).catch(() => {});
      assert.doesNotMatch(await forgotten.page('v1').textContent('#who'), /visitor=ada/);
    } finally {
      await forgotten.stop();
    }

    // With it: the same sign-in, in a browser that has never been there.
    const remembered = await startSession('d1', { storageState: state });
    try {
      await remembered.openView('v1', `${site.origin}/`);
      assert.match(await remembered.page('v1').textContent('#who'), /visitor=ada/);
      await remembered.clearStorage();
      await remembered.page('v1').reload();
      assert.doesNotMatch(
        await remembered.page('v1').textContent('#who'),
        /visitor=ada/,
        'erasing is erasing, in the browser that is open and not only on disk',
      );
    } finally {
      await remembered.stop();
    }
  });
});
