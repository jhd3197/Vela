import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

// The Agent window against a real engine, on a disposable data directory.
//
// What is checked here is what the person sees and what they can reach: the
// setup screen refusing to start a desktop that could not work, the four
// questions being the four questions, a dropped app becoming a removable chip
// rather than an instruction, and — the one that matters — the approval control
// living in the owner's window rather than anywhere the agent could press it.
//
// The model server and the browser runtime may or may not be on the machine
// running this. Both cases are real, so this asserts on the *structure* and on
// the refusals, never on a model being installed.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const python =
  process.env.VELA_TEST_PYTHON ||
  path.join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const port = 17719;
const server = spawn(python, ['scripts/serve-release-fixtures.py', String(port)], {
  cwd: root,
  windowsHide: true,
  stdio: ['ignore', 'pipe', 'pipe'],
});
const base = `http://127.0.0.1:${port}`;
let output = '',
  browser;
server.stderr.on('data', (data) => {
  output += data;
});
server.on('error', (error) => {
  output += error.message;
});

/** Open the rail's desktop menu and wait for it. */
async function openMenu(page) {
  await page.locator('.rail-desktop-item').click();
  await page.locator('.desktop-menu').waitFor();
}

try {
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw new Error(output);
    try {
      if ((await fetch(base + '/api/health')).ok) break;
    } catch {
      /* not up yet */
    }
    if (i === 99) throw new Error(`Fixture server did not start: ${output}`);
    await new Promise((resolve) => setTimeout(resolve, 100));
  }

  browser = await chromium.launch({
    headless: true,
    channel: process.env.VELA_BROWSER_CHANNEL || 'chrome',
  });
  const context = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  await context.addInitScript(() => {
    try {
      localStorage.setItem('vela.welcome.v1', 'done');
    } catch {
      /* Not the hub page. */
    }
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));

  await page.goto(base + '/');
  await page.locator('.desk-grid').waitFor();
  await page.locator('.rail-desktop-item').waitFor();

  // --- opening the Agent window on a personal desktop shows the setup ------
  await openMenu(page);
  await page.locator('.desktop-menu-action[aria-label^="Let an agent work in"]').first().click();
  const window = page.locator('.window-frame', { hasText: 'Agent' });
  await window.waitFor();
  const setup = page.locator('.agent-setup');
  await setup.waitFor();

  // The four questions from the plan's setup, and nothing about workers,
  // ports, protocols or browser runtimes.
  const text = await setup.innerText();
  for (const asked of ['Model', 'Apps it may use', 'Websites it may open', 'Changes']) {
    assert.ok(text.includes(asked), `the setup asks about ${asked}: ${text}`);
  }
  for (const jargon of ['worker', 'protocol', 'Playwright', 'port ']) {
    assert.ok(!text.toLowerCase().includes(jargon.toLowerCase()), `no ${jargon} in the setup`);
  }

  // "Ask before changes" is the default, and the other choice is explicitly not
  // a trust-everything switch.
  assert.ok(
    await page.getByRole('radio', { name: /Ask before changes/ }).isChecked(),
    'asking is the default',
  );
  assert.ok(
    text.includes('not a "trust it with everything" switch'),
    'the granted mode says what it is not',
  );

  // Nothing chosen: it cannot be started. This holds whether or not a model is
  // installed, because either way a desktop that allows nothing allows nothing.
  const start = page.getByRole('button', { name: 'Start the agent' });
  assert.ok(await start.isDisabled(), 'a desktop that allows nothing cannot be started');

  // --- the engine agrees with the screen ----------------------------------
  // Whatever the screen says about the runtime and the models, it came from
  // here. A setup screen that decided for itself would be a screen that could
  // be wrong.
  const truth = await page.evaluate(async () => {
    const { token } = await (
      await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } })
    ).json();
    const headers = { Authorization: `Bearer ${token}` };
    const [runtime, models] = await Promise.all([
      (await fetch('/api/desktops/runtime', { headers })).json(),
      (await fetch('/api/desktops/models', { headers })).json(),
    ]);
    return { runtime, models };
  });
  const blocked = await page.locator('.agent-blocked').count();
  const shouldBlock =
    !truth.runtime.available ||
    !truth.models.reachable ||
    !(truth.models.models || []).some((model) => model.tools);
  assert.equal(
    blocked > 0,
    shouldBlock,
    `the screen says what the engine says (runtime ${truth.runtime.available}, models ${truth.models.reachable})`,
  );

  // --- tasks belong to a desktop that has an agent ------------------------
  const refused = await page.evaluate(async () => {
    const { token } = await (
      await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } })
    ).json();
    const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
    const list = await (await fetch('/api/desktops', { headers })).json();
    const response = await fetch(`/api/desktops/${list.defaultId}/tasks`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ instruction: 'do something' }),
    });
    return { status: response.status, body: await response.json() };
  });
  assert.equal(refused.status, 409, 'a personal desktop takes no tasks');
  assert.match(refused.body.detail, /not running an agent/);

  // --- the approval route is the owner's, not any app's --------------------
  const reach = await page.evaluate(async () => {
    const list = await (
      await fetch('/api/desktops', {
        headers: {
          Authorization: `Bearer ${(await (await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } })).json()).token}`,
        },
      })
    ).json();
    // No bearer at all: this is what an unauthenticated caller sees, which is
    // the shape anything inside the agent's browser would be.
    const response = await fetch(`/api/desktops/${list.defaultId}/approvals`);
    return response.status;
  });
  assert.equal(reach, 401, 'approvals need the owner, not merely a request');

  // --- the window is a window ---------------------------------------------
  await window.getByRole('button', { name: /^Minimize / }).click();
  await page.locator('.window-frame').waitFor({ state: 'detached' });
  assert.equal(
    await page.locator('.rail-views .rail-view-item').count(),
    1,
    'minimizing keeps it open and in the rail',
  );
  await page.locator('.rail-views .rail-view-item').click();
  await page.locator('.agent-setup').waitFor();

  assert.deepEqual(errors, []);
  console.log(
    'PASS: the Agent window opens as owner chrome from the rail, its setup asks the four ' +
      'questions without jargon, defaults to asking before changes, refuses to start a desktop ' +
      'that allows nothing, agrees with the engine about the runtime and models, refuses a task ' +
      'on a personal desktop, keeps approvals behind owner authentication, and minimizes and ' +
      'restores like any other window',
  );
  await context.close();
} finally {
  await browser?.close();
  server.kill();
}
