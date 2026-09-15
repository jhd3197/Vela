import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

// Serve the real built dashboard with disposable API fixtures and controllable SSE.
const root = fileURLToPath(new URL('../..', import.meta.url));
const dist = path.join(root, 'web/dist');
const shots = path.join(root, 'docs/screenshots/chat');
const requests = [];
const streams = [];
let reachable = true;
let retention = true;
const apps = [
  { id: 'meals', name: 'Meal Planner', installed: true, running: true },
  { id: 'notes', name: 'Notes', installed: true, running: false },
  { id: 'catalog-only', name: 'Not installed', installed: false },
];
const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/api/chat') {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    requests.push(JSON.parse(raw));
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
    res.write(`data: ${JSON.stringify({ conversationId: 'fixture-conversation' })}\n\n`);
    streams.push(res);
    return;
  }
  if (url.pathname.startsWith('/api/')) {
    const responses = {
      '/api/session': { token: 'fixture', remote: false },
      '/api/apps': { apps },
      '/api/engine': { apps_running: 1, storage_bytes: 2048, version: 'fixture' },
      '/api/settings': { chat_history: retention },
      '/api/ai/status': { reachable, models: ['qwen3:8b'] },
      '/api/notifications': { notifications: [] },
      '/api/platforms': {},
    };
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(responses[url.pathname] ?? {}));
    return;
  }
  const relative = url.pathname.replace(/^\/+/, '');
  const candidate = path.resolve(dist, relative);
  const file =
    candidate.startsWith(dist + path.sep) && path.extname(relative)
      ? candidate
      : path.join(dist, 'index.html');
  try {
    const types = {
      '.html': 'text/html',
      '.js': 'text/javascript',
      '.css': 'text/css',
      '.png': 'image/png',
      '.svg': 'image/svg+xml',
    };
    res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' });
    res.end(await readFile(file));
  } catch {
    res.writeHead(404);
    res.end();
  }
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const emit = (event) => streams.at(-1).write(`data: ${JSON.stringify(event)}\n\n`);
const finish = (text) => {
  emit({ text, done: true });
  streams.at(-1).end();
};
let browser;
try {
  browser = await chromium.launch({
    headless: true,
    channel: process.env.VELA_BROWSER_CHANNEL || 'chrome',
  });
  const context = await browser.newContext({ serviceWorkers: 'block' });
  await context.addInitScript(() => {
    // Exercise the copy control without changing the user's system clipboard.
    Object.defineProperty(navigator, 'clipboard', {
      value: {
        writeText: async (text) => {
          window.chatTestClipboard = text;
        },
      },
    });
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await mkdir(shots, { recursive: true });
  const input = page.getByRole('combobox', { name: 'Ask a question' });
  const send = page.getByRole('button', { name: 'Send', exact: true });
  const checkLayout = async (width) => {
    const boxes = await page.evaluate(() => {
      const composer = document.querySelector('.chat-composer').getBoundingClientRect();
      const main = document.querySelector('.workspace-content');
      const nav = document.querySelector('.tabbar').getBoundingClientRect();
      return {
        bottom: composer.bottom,
        navTop: nav.top,
        bodyOverflow: document.documentElement.scrollWidth - innerWidth,
        pageOverflow: main.scrollHeight - main.clientHeight,
        height: innerHeight,
      };
    });
    assert.ok(boxes.bodyOverflow <= 1, JSON.stringify(boxes));
    assert.ok(boxes.pageOverflow <= 1, JSON.stringify(boxes));
    assert.ok(
      boxes.bottom <= (width < 860 ? boxes.navTop : boxes.height) &&
        boxes.bottom > boxes.height - 160,
      JSON.stringify(boxes),
    );
  };
  for (const viewport of [
    { width: 1366, height: 900 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    for (const theme of ['light', 'dark']) {
      await page.goto(base + '/ask');
      await page.getByText('Connected to your server', { exact: true }).waitFor();
      await page.addStyleTag({
        content:
          '*, *::before, *::after { transition: none !important; animation: none !important; }',
      });
      await page.evaluate((theme) => {
        document.documentElement.dataset.theme = theme;
      }, theme);
      await checkLayout(viewport.width);
      await page.screenshot({ path: path.join(shots, `empty-${theme}-${viewport.width}.png`) });
      await input.fill('Check @mea');
      await page.getByRole('option', { name: /Meal Planner/ }).waitFor();
      assert.equal(await page.getByRole('option').count(), 1);
      await page.screenshot({ path: path.join(shots, `mention-${theme}-${viewport.width}.png`) });
      await input.press('Enter');
      assert.equal(await input.inputValue(), 'Check @meals ');
      assert.equal(requests.length, 0, 'Selecting a mention must not send');
    }
  }
  await page.setViewportSize({ width: 1366, height: 900 });
  await input.fill('Compare @');
  assert.equal(await page.getByRole('option').count(), 2, 'Catalog-only apps excluded');
  await input.press('ArrowDown');
  await input.press('Enter');
  assert.equal(await input.inputValue(), 'Compare @notes ');
  await input.fill('@unknown');
  await page.getByText('No installed apps match.', { exact: false }).waitFor();
  await input.press('Enter');
  assert.equal(requests.length, 0);
  await input.press('Escape');
  assert.equal(await input.getAttribute('aria-expanded'), 'false');
  await input.fill('Check ');
  await page.getByRole('button', { name: 'Mention an app' }).click();
  await page.getByRole('option', { name: /Meal Planner/ }).click();
  assert.equal(await input.inputValue(), 'Check @meals ');
  await input.press('Shift+Enter');
  await input.pressSequentially('Show logs');
  assert.match(await input.inputValue(), /\nShow logs$/);
  await input.dispatchEvent('keydown', { key: 'Enter', isComposing: true });
  assert.equal(requests.length, 0, 'IME confirmation must not send');
  await input.press('Enter');
  await page.getByRole('button', { name: 'Stop response' }).waitFor();
  assert.equal(requests.length, 1);
  assert.match(requests[0].messages[0].content, /@meals/);
  emit({ activity: { id: 1, tool: 'app_logs', state: 'running' } });
  emit({ text: 'Partial answer that must survive stopping.' });
  await page.getByText('Partial answer that must survive stopping.', { exact: true }).waitFor();
  await input.fill('Keep this draft');
  await page.getByRole('button', { name: 'Stop response' }).click();
  await page
    .getByText('Response stopped.', { exact: true })
    .waitFor({ timeout: 5000 })
    .catch(async (error) => {
      console.error(await page.locator('.ask-workspace').innerText(), errors);
      throw error;
    });
  assert.equal(
    await page.getByText('Partial answer that must survive stopping.', { exact: true }).count(),
    1,
  );
  assert.equal(await page.locator('.tool-call[data-state="running"]').count(), 0);
  await page.getByRole('button', { name: 'Try again' }).click();
  await page.getByRole('button', { name: 'Stop response' }).waitFor();
  assert.equal(await input.inputValue(), 'Keep this draft');
  assert.equal(
    await page.locator('.chat-user').count(),
    1,
    'Retry must not duplicate the question',
  );
  assert.equal(requests.length, 2);
  emit({ activity: { id: 2, tool: 'app_logs', state: 'complete' } });
  const answer =
    '## App status\n\n**Meal Planner** is running.\n\n- Logs are available\n- No recent errors\n\n```sh\n' +
    'long-log-entry '.repeat(40) +
    '\n```\n\n| App | State |\n| --- | --- |\n| Meals | Running |\n\n[Unsafe](javascript:alert(1)) <script>alert(1)</script>';
  finish(answer);
  await page.getByRole('heading', { name: 'App status' }).waitFor();
  await send.waitFor();
  assert.equal(await page.locator('.chat-markdown table').count(), 1);
  assert.equal(
    await page.locator('.chat-markdown script, .chat-markdown a[href^="javascript:"]').count(),
    0,
  );
  await page.getByRole('button', { name: 'Copy', exact: true }).click();
  assert.equal(await page.evaluate(() => window.chatTestClipboard), answer);
  await page.locator('.tool-details summary').click();
  await page.getByText('app_logs', { exact: true }).waitFor();
  await checkLayout(1366);
  await page.screenshot({ path: path.join(shots, 'conversation-desktop.png') });
  // Long streamed output follows the bottom until the reader scrolls up.
  await input.fill('A longer answer');
  await input.press('Enter');
  await page.getByRole('button', { name: 'Stop response' }).waitFor();
  const long = Array.from({ length: 50 }, (_, i) => `Paragraph ${i}: server information.\n\n`).join(
    '',
  );
  emit({ text: long });
  await page.getByText('Paragraph 49: server information.', { exact: true }).waitFor();
  await page.waitForFunction(() => {
    const log = document.querySelector('.chat-log');
    return log.scrollHeight - log.scrollTop - log.clientHeight < 5;
  });
  await page.locator('.chat-log').evaluate((log) => {
    log.scrollTop = 0;
  });
  await page.getByRole('button', { name: 'Jump to latest' }).waitFor();
  emit({ text: long + 'New streamed content' });
  await page.getByText('New streamed content', { exact: true }).waitFor();
  assert.equal(await page.locator('.chat-log').evaluate((log) => log.scrollTop), 0);
  await checkLayout(1366);
  await page.getByRole('button', { name: 'Jump to latest' }).click();
  finish(long + 'New streamed content');
  await send.waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await checkLayout(390);
  await page.screenshot({ path: path.join(shots, 'conversation-phone.png') });
  await page.reload();
  await page.getByText('New streamed content', { exact: true }).waitFor();
  retention = false;
  await page.reload();
  await page.getByRole('heading', { name: 'What should I look into?' }).waitFor();
  assert.equal(await page.evaluate(() => localStorage.getItem('vela-chat')), null);
  reachable = false;
  await page.reload();
  await page.getByText('The assistant is offline.', { exact: true }).waitFor();
  await input.fill('My draft while offline');
  assert.equal(await send.isDisabled(), true);
  await checkLayout(390);
  reachable = true;
  await page.getByRole('button', { name: 'Reconnect' }).click();
  await page.getByText('Connected to your server', { exact: true }).waitFor();
  assert.equal(await input.inputValue(), 'My draft while offline');
  assert.equal(await send.isEnabled(), true);
  assert.deepEqual(errors, []);
  console.log(
    'PASS: bottom composer, desktop/phone themes, app mentions, keyboard/IME input, Markdown safety, copy, stop/retry, draft preservation, stream scrolling, retention and offline recovery.',
  );
} finally {
  await browser?.close();
  for (const stream of streams) stream.end();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
