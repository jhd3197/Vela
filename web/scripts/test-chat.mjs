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
// A disposable stand-in for the server's conversation store, with the same
// shape as /api/chat/conversations. Nothing here touches real data.
const store = new Map();
let legacyImported = false;
let sequence = 0;
const summary = (conversation) => ({
  id: conversation.id,
  title: conversation.title,
  createdAt: conversation.createdAt,
  updatedAt: conversation.updatedAt,
  archived: conversation.archived,
  messageCount: conversation.messages.length,
  preview: conversation.messages.at(-1)?.content.slice(0, 140) ?? '',
});
const newConversation = () => {
  const stamp = new Date(Date.now() + ++sequence).toISOString();
  const conversation = {
    id: `conversation-${sequence}`,
    title: 'New conversation',
    createdAt: stamp,
    updatedAt: stamp,
    archived: false,
    draft: '',
    messages: [],
  };
  store.set(conversation.id, conversation);
  return conversation;
};
const apps = [
  { id: 'meals', name: 'Meal Planner', installed: true, running: true },
  { id: 'notes', name: 'Notes', installed: true, running: false },
  { id: 'catalog-only', name: 'Not installed', installed: false },
];
const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const body = async () => {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    return raw ? JSON.parse(raw) : {};
  };
  const json = (value, status = 200) => {
    res.writeHead(status, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(value));
  };
  if (url.pathname === '/api/chat') {
    const payload = await body();
    requests.push(payload);
    const conversation = store.get(payload.conversationId);
    if (retention && conversation) {
      conversation.messages.push({
        id: `m-${++sequence}`,
        role: 'user',
        content: payload.messages.at(-1).content,
      });
      if (conversation.title === 'New conversation')
        conversation.title = payload.messages.at(-1).content.slice(0, 60);
      conversation.updatedAt = new Date(Date.now() + sequence).toISOString();
    }
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
    res.write(
      `data: ${JSON.stringify({
        conversationId: payload.conversationId ?? 'fixture-conversation',
        title: conversation?.title,
      })}

`,
    );
    streams.push({ res, conversation });
    return;
  }
  if (url.pathname === '/api/chat/conversations/import') {
    await body();
    const already = legacyImported;
    legacyImported = true;
    return json({ imported: !already, conversationId: null });
  }
  if (url.pathname === '/api/chat/conversations') {
    if (!retention) return json({ conversations: [], enabled: false });
    if (req.method === 'POST') return json(newConversation(), 201);
    const query = (url.searchParams.get('query') || '').toLowerCase();
    const archived = url.searchParams.get('archived') === 'true';
    const conversations = [...store.values()]
      .filter((conversation) => conversation.archived === archived)
      .filter(
        (conversation) =>
          !query ||
          conversation.title.toLowerCase().includes(query) ||
          conversation.messages.some((message) => message.content.toLowerCase().includes(query)),
      )
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
      .map(summary);
    return json({ conversations, enabled: true });
  }
  if (url.pathname.startsWith('/api/chat/conversations/')) {
    if (!retention) return json({ detail: 'Chat history is turned off' }, 409);
    const id = decodeURIComponent(url.pathname.split('/').pop());
    const conversation = store.get(id);
    if (!conversation) return json({ detail: 'conversation not found' }, 404);
    if (req.method === 'DELETE') {
      store.delete(id);
      res.writeHead(204);
      return res.end();
    }
    if (req.method === 'PATCH') {
      const patch = await body();
      if (patch.title !== undefined) conversation.title = patch.title;
      if (patch.archived !== undefined) conversation.archived = patch.archived;
      if (patch.draft !== undefined) conversation.draft = patch.draft;
      if (patch.title !== undefined || patch.archived !== undefined)
        conversation.updatedAt = new Date(Date.now() + ++sequence).toISOString();
    }
    return json({
      ...summary(conversation),
      draft: conversation.draft,
      messages: conversation.messages,
    });
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
const emit = (event, index = -1) =>
  streams.at(index).res.write(`data: ${JSON.stringify(event)}

`);
const finish = (text, index = -1) => {
  const stream = streams.at(index);
  emit({ text, done: true }, index);
  if (retention && stream.conversation)
    stream.conversation.messages.push({ id: `m-${++sequence}`, role: 'assistant', content: text });
  stream.res.end();
};
const settle = async (predicate, label) => {
  for (let attempt = 0; attempt < 200; attempt++) {
    if (await predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  assert.ok(await predicate(), label);
};
// The first question creates the conversation before it streams, so the test
// waits for the request instead of assuming it is already in flight.
const waitForStream = (count) => settle(() => streams.length >= count, `stream ${count}`);

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
  const checkLayout = async () => {
    const boxes = await page.evaluate(() => {
      const composer = document.querySelector('.chat-composer').getBoundingClientRect();
      const main = document.querySelector('.workspace-content');
      return {
        bottom: composer.bottom,
        bodyOverflow: document.documentElement.scrollWidth - innerWidth,
        pageOverflow: main.scrollHeight - main.clientHeight,
        height: innerHeight,
      };
    });
    assert.ok(boxes.bodyOverflow <= 1, JSON.stringify(boxes));
    assert.ok(boxes.pageOverflow <= 1, JSON.stringify(boxes));
    assert.ok(
      boxes.bottom <= boxes.height && boxes.bottom > boxes.height - 160,
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
      await page.getByText('Vela · qwen3:8b', { exact: true }).waitFor();
      await page.addStyleTag({
        content:
          '*, *::before, *::after { transition: none !important; animation: none !important; }',
      });
      await page.evaluate((theme) => {
        document.documentElement.dataset.theme = theme;
      }, theme);
      await checkLayout();
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
  await waitForStream(1);
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
  await waitForStream(2);
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
  await checkLayout();
  await page.screenshot({ path: path.join(shots, 'conversation-desktop.png') });
  // Long streamed output follows the bottom until the reader scrolls up.
  await input.fill('A longer answer');
  await input.press('Enter');
  await page.getByRole('button', { name: 'Stop response' }).waitFor();
  await waitForStream(3);
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
  await checkLayout();
  await page.getByRole('button', { name: 'Jump to latest' }).click();
  finish(long + 'New streamed content');
  await send.waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await checkLayout();
  await page.screenshot({ path: path.join(shots, 'conversation-phone.png') });
  // On a phone the header's one control opens the conversation list in a
  // drawer beside the rail, which stays on screen.
  assert.equal(await page.getByRole('button', { name: 'Open navigation' }).count(), 0);
  assert.equal(await page.locator('.rail').isVisible(), true);
  await page.getByRole('button', { name: 'Show conversations' }).click();
  const phoneDrawer = page.getByRole('dialog', { name: 'Conversations' });
  await phoneDrawer.locator('.conversation-panel').waitFor();
  assert.equal(await phoneDrawer.locator('.rail').count(), 0);
  await page.screenshot({ path: path.join(shots, 'conversation-phone-drawer.png') });
  await phoneDrawer.getByRole('button', { name: 'New conversation' }).click();
  await phoneDrawer.waitFor({ state: 'detached' });
  await page.getByRole('heading', { name: 'What should I look into?' }).waitFor();
  await page.goBack();
  await page.getByText('New streamed content', { exact: true }).waitFor();
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
  await checkLayout();
  reachable = true;
  await page.getByRole('button', { name: 'Reconnect' }).click();
  await page.getByText('Vela · qwen3:8b', { exact: true }).waitFor();
  assert.equal(await input.inputValue(), 'My draft while offline');
  assert.equal(await send.isEnabled(), true);
  // ---- durable conversations ----------------------------------------
  retention = true;
  store.clear();
  await page.setViewportSize({ width: 1366, height: 900 });

  // The transcript the browser used to hold is imported once and then removed.
  legacyImported = false;
  await page.evaluate(() =>
    localStorage.setItem(
      'vela-chat',
      JSON.stringify([{ role: 'user', content: 'Question from the old transcript' }]),
    ),
  );
  await page.goto(base + '/ask');
  await settle(() => legacyImported, 'legacy transcript imported');
  await settle(
    async () => (await page.evaluate(() => localStorage.getItem('vela-chat'))) === null,
    'legacy transcript cleared',
  );
  legacyImported = false;
  await page.reload();
  await page.waitForTimeout(300);
  assert.equal(legacyImported, false, 'a cleared transcript is never re-imported');
  await page.getByRole('heading', { name: 'What should I look into?' }).waitFor();

  const transcript = page.getByRole('region', { name: 'Conversation' });
  const askAndAnswer = async (question, answer) => {
    const before = streams.length;
    await input.fill(question);
    await input.press('Enter');
    await waitForStream(before + 1);
    finish(answer);
    await send.waitFor();
    await transcript.getByText(answer, { exact: true }).waitFor();
  };

  await askAndAnswer('First conversation question', 'First conversation answer');
  const firstUrl = page.url();
  assert.match(firstUrl, /\/ask\/[^/]+$/, 'the conversation is part of the route');

  // Starting another conversation must not erase the previous one.
  await page.getByRole('button', { name: 'New conversation', exact: true }).first().click();
  await page.getByRole('heading', { name: 'What should I look into?' }).waitFor();
  await askAndAnswer('Second conversation question', 'Second conversation answer');
  const secondUrl = page.url();
  assert.notEqual(firstUrl, secondUrl);

  const panel = page.locator('.conversation-panel');
  const entry = (name) => panel.locator('.conversation-open').filter({ hasText: name });
  await entry('First conversation question').waitFor();
  assert.equal(await panel.locator('.conversation-open').count(), 2);

  // Selecting history loads that conversation, not the one on screen.
  await entry('First conversation question').click();
  await transcript.getByText('First conversation answer', { exact: true }).waitFor();
  assert.equal(await transcript.getByText('Second conversation answer').count(), 0);
  assert.equal(page.url(), firstUrl);

  // A reload restores the same conversation from the route.
  await page.reload();
  await transcript.getByText('First conversation answer', { exact: true }).waitFor();
  assert.equal(await transcript.getByText('Second conversation answer').count(), 0);

  // A follow-up carries the stored conversation id, so the server can rebuild
  // its context rather than starting an unrelated one.
  const beforeFollowUp = requests.length;
  await askAndAnswer('Follow-up question', 'Follow-up answer');
  assert.equal(
    requests[beforeFollowUp].conversationId,
    firstUrl.split('/').pop(),
    'a follow-up stays bound to its conversation',
  );

  // Per-conversation drafts survive switching and reloading.
  await input.fill('Draft kept with the first conversation');
  await settle(
    () => [...store.values()].some((c) => c.draft === 'Draft kept with the first conversation'),
    'draft saved',
  );
  await entry('Second conversation question').click();
  await transcript.getByText('Second conversation answer', { exact: true }).waitFor();
  assert.equal(await input.inputValue(), '');
  await entry('First conversation question').click();
  await transcript.getByText('First conversation answer', { exact: true }).waitFor();
  assert.equal(await input.inputValue(), 'Draft kept with the first conversation');
  await input.fill('');

  // Search narrows the list to conversations that actually match.
  const search = page.getByRole('searchbox', { name: 'Search conversations' });
  await search.fill('Second');
  // The list is filtered by a request, so wait for it to narrow rather than
  // reading the count while the unfiltered list is still on screen. The entry
  // being searched for is present either way, so waiting for it proves nothing.
  const listed = () => panel.locator('.conversation-open').count();
  await settle(async () => (await listed()) === 1, 'the search narrowed the list');
  await entry('Second conversation question').waitFor();
  assert.equal(await listed(), 1);
  await search.fill('no such conversation');
  await page.getByText('No conversation matches', { exact: false }).waitFor();
  await search.fill('');
  await entry('First conversation question').waitFor();

  // Rename, archive and restore are distinct from permanent deletion.
  await panel.getByRole('button', { name: 'Actions for Second conversation question' }).click();
  await page.getByRole('menuitem', { name: 'Rename' }).click();
  const rename = page.getByRole('dialog', { name: 'Rename conversation' });
  await rename.getByLabel('Title').fill('Storage review');
  await rename.getByRole('button', { name: 'Save', exact: true }).click();
  await entry('Storage review').waitFor();

  await panel.getByRole('button', { name: 'Actions for Storage review' }).click();
  await page.getByRole('menuitem', { name: 'Archive' }).click();
  await entry('Storage review').waitFor({ state: 'detached' });
  await panel.getByRole('button', { name: 'Archived' }).click();
  await entry('Storage review').waitFor();
  await panel.getByRole('button', { name: 'Actions for Storage review' }).click();
  await page.getByRole('menuitem', { name: 'Restore' }).click();
  await panel.getByRole('button', { name: 'Archived' }).click();
  await entry('Storage review').waitFor();

  await panel.getByRole('button', { name: 'Actions for Storage review' }).click();
  await page.getByRole('menuitem', { name: 'Delete' }).click();
  const confirm = page.getByRole('dialog', { name: 'Delete this conversation?' });
  await confirm.getByRole('button', { name: 'Delete permanently' }).click();
  await entry('Storage review').waitFor({ state: 'detached' });
  assert.equal(await panel.locator('.conversation-open').count(), 1);

  // An inaccessible conversation id explains itself instead of looking empty.
  await page.goto(base + '/ask/conversation-that-does-not-exist');
  await page.getByText('That conversation is no longer available.', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Start a new conversation' }).click();
  await page.getByRole('heading', { name: 'What should I look into?' }).waitFor();

  // Switching conversations cancels the request in flight; its events can never
  // land in the conversation now on screen.
  await page.goto(firstUrl);
  await transcript.getByText('First conversation answer', { exact: true }).waitFor();
  const openStreams = streams.length;
  await input.fill('A question that gets abandoned');
  await input.press('Enter');
  await waitForStream(openStreams + 1);
  const abandoned = streams[streams.length - 1];
  await page.getByRole('button', { name: 'New conversation', exact: true }).first().click();
  await page.getByRole('heading', { name: 'What should I look into?' }).waitFor();
  abandoned.res.write(
    `data: ${JSON.stringify({ text: 'Answer for the abandoned conversation', done: true })}\n\n`,
  );
  abandoned.res.end();
  await page.waitForTimeout(400);
  assert.equal(
    await transcript.getByText('Answer for the abandoned conversation').count(),
    0,
    'a stale stream must never write into another conversation',
  );

  // Retention off hides history and says so rather than pretending to save.
  retention = false;
  await page.reload();
  await page.getByText('Chat history is turned off', { exact: false }).waitFor();
  assert.equal(await panel.locator('.conversation-open').count(), 0);
  retention = true;

  assert.deepEqual(errors, []);
  console.log(
    'PASS: composer, themes, mentions, IME input, Markdown safety, copy, stop/retry, stream scrolling, offline recovery, and durable conversations (create, switch, reload, follow-up binding, drafts, search, rename, archive, delete, missing id, stale stream, retention off).',
  );
} finally {
  await browser?.close();
  for (const stream of streams) stream.res.end();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
