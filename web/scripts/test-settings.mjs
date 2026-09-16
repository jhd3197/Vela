import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

// Built dashboard with disposable API responses; never accesses installed data.
const root = fileURLToPath(new URL('../..', import.meta.url));
const dist = path.join(root, 'web/dist');
const shots = path.join(root, 'docs/screenshots/settings');
const browser = await chromium.launch({
  headless: true,
  channel: process.env.VELA_BROWSER_CHANNEL || 'chrome',
});
const errors = [];
try {
  await fs.mkdir(shots, { recursive: true });
  const context = await browser.newContext({
    viewport: { width: 1366, height: 900 },
    serviceWorkers: 'block',
  });
  await context.addInitScript(() => localStorage.setItem('vela.welcome.v1', 'done'));
  let settings = { theme: 'light', chat_history: true, ntfy_config: {} };
  let failSave = false;
  // Health: nothing until Run now is pressed, then one broken check that the
  // repair fixes — the two states the section has to get right.
  const brokenCheck = {
    key: 'stale-apps',
    title: 'App state',
    status: 'fail',
    detail: 'Vela still lists Notes as running, but the process is gone.',
    repairable: true,
    ranAt: '2026-09-16T09:00:00',
  };
  const fixedCheck = {
    ...brokenCheck,
    status: 'ok',
    detail: 'Every app Vela lists as running really is.',
    repairable: false,
  };
  const passingCheck = {
    key: 'data-dir',
    title: 'Room to work',
    status: 'ok',
    detail: '120 GB free where Vela keeps your data.',
    repairable: false,
    ranAt: '2026-09-16T09:00:00',
  };
  const skippedCheck = {
    key: 'certificate',
    title: 'Certificate',
    status: 'skipped',
    detail: 'Vela is not using HTTPS on this computer.',
    repairable: false,
    ranAt: '2026-09-16T09:00:00',
  };
  // Backups: one fixture backup, a schedule that starts off, and a restore
  // that reports the safety copy it took.
  let backupList = [
    { name: '20260916-030000', size: 40960, created_at: '2026-09-16T03:00:00', safety: false },
  ];
  let backupSchedule = {
    enabled: false,
    time: '03:00',
    keep: 10,
    timezone: 'Europe/Madrid',
    nextRunAt: null,
  };
  let restored = null;

  // Updates: nothing known until Check now, then a newer release with notes.
  let updateState = {
    current: '0.1.10',
    latest: null,
    available: false,
    notes: '',
    asset: null,
    capability: 'portable',
    checkedAt: null,
    error: null,
    check: true,
    mode: 'notify',
    hour: 3,
  };
  let updateChecks = 0;
  let updateJob = { state: 'idle', percent: 0, message: '', rollback: false };
  let applied = false;

  let doctorRan = false;
  let doctorRepaired = false;
  let doctorRuns = 0;
  const doctorBody = () => {
    if (!doctorRan)
      return { checks: [], ranAt: null, summary: { text: 'Vela has not checked itself yet.' } };
    const checks = [doctorRepaired ? fixedCheck : brokenCheck, passingCheck, skippedCheck];
    const attention = checks.filter((c) => c.status === 'fail' || c.status === 'warn').length;
    return {
      checks,
      ranAt: '2026-09-16T09:00:00',
      summary: {
        attention,
        considered: 2,
        text: attention ? `${attention} of 2 checks need attention.` : 'All 2 checks passed.',
      },
    };
  };
  await context.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.hostname !== 'vela.test') return route.abort();
    if (url.pathname.startsWith('/api/')) {
      if (url.pathname === '/api/updates') return route.fulfill({ json: updateState });
      if (url.pathname === '/api/updates/job') return route.fulfill({ json: updateJob });
      if (url.pathname === '/api/updates/report') return route.fulfill({ json: {} });
      if (url.pathname === '/api/updates/apply') {
        if (route.request().headers()['x-vela-confirm'] !== 'update')
          return route.fulfill({ status: 428, json: { detail: 'Confirm installing this update' } });
        applied = true;
        updateJob = { state: 'downloading', percent: 40, message: '', rollback: false };
        return route.fulfill({ json: updateJob });
      }
      if (url.pathname === '/api/updates/check') {
        updateChecks += 1;
        if (!updateState.check) return route.fulfill({ json: { ...updateState, skipped: 'off' } });
        updateState = {
          ...updateState,
          latest: '0.2.0',
          available: true,
          checkedAt: '2026-09-16T09:00:00',
          notes: [
            '## What changed',
            '',
            '- A new desk',
            '- ![shot](https://example.test/a.png)',
            '- [Read more](https://example.test/notes)',
          ].join('\n'),
          asset: { name: 'vela-server-0.2.0-windows-x64.zip', size: 1024 },
        };
        return route.fulfill({ json: updateState });
      }
      if (url.pathname === '/api/backups') return route.fulfill({ json: { backups: backupList } });
      if (url.pathname === '/api/backups/stats')
        return route.fulfill({
          json: {
            count: backupList.length,
            totalSize: backupList.reduce((sum, entry) => sum + entry.size, 0),
            lastSuccessAt: backupList.find((entry) => !entry.safety)?.created_at || null,
            lastName: backupList.find((entry) => !entry.safety)?.name || null,
            keep: backupSchedule.keep,
            schedule: backupSchedule,
          },
        });
      if (url.pathname.endsWith('/restore')) {
        if (route.request().headers()['x-vela-confirm'] !== 'restore')
          return route.fulfill({ status: 428, json: { detail: 'Confirm restoring this backup' } });
        restored = url.pathname.split('/')[3];
        backupList = [
          {
            name: 'pre-restore-20260916-094500',
            size: 40960,
            created_at: '2026-09-16T09:45:00',
            safety: true,
          },
          ...backupList,
        ];
        return route.fulfill({
          json: {
            name: restored,
            safety: 'pre-restore-20260916-094500',
            restored: ['settings.json'],
            stopped: [],
            restarted: [],
            failedToRestart: [],
          },
        });
      }
      if (url.pathname === '/api/doctor') return route.fulfill({ json: doctorBody() });
      if (url.pathname === '/api/doctor/run') {
        doctorRan = true;
        doctorRuns += 1;
        return route.fulfill({ json: doctorBody() });
      }
      if (url.pathname === '/api/doctor/stale-apps/repair') {
        doctorRepaired = true;
        return route.fulfill({
          json: { ok: true, detail: 'Cleared the stale record for Notes.', check: fixedCheck },
        });
      }
      if (url.pathname === '/api/settings') {
        if (route.request().method() === 'PATCH') {
          if (failSave)
            return route.fulfill({ status: 500, json: { detail: 'Fixture save failure' } });
          const patch = route.request().postDataJSON();
          if (patch.updates) updateState = { ...updateState, ...patch.updates };
          if (patch.backups?.schedule) {
            backupSchedule = {
              ...backupSchedule,
              ...patch.backups.schedule,
              nextRunAt: patch.backups.schedule.enabled ? '2026-09-17T03:00:00+02:00' : null,
            };
          }
          settings = { ...settings, ...patch };
          // The engine derives the avatar letter from the display name rather
          // than accepting one, so the fixture has to as well or the rail would
          // never draw it here.
          if (patch.identity) {
            const identity = { ...settings.identity };
            const source = identity.displayName || identity.serverName || '';
            const letter = [...source].find((character) => /[\p{L}\p{N}]/u.test(character)) || '';
            settings = { ...settings, identity: { ...identity, initial: letter.toUpperCase() } };
          }
        }
        return route.fulfill({ json: settings });
      }
      const responses = {
        '/api/session': { token: 'fixture', remote: false },
        '/api/apps': { apps: [] },
        '/api/engine': {
          version: 'fixture',
          apps_running: 0,
          storage_bytes: 2048,
          data_dir: '/fixture/data',
        },
        '/api/health': { version: '0.1.0' },
        '/api/platforms': { current: 'windows', supported: ['windows'] },
        '/api/notifications': { notifications: [] },
        '/api/ai/status': {
          reachable: true,
          models: ['fixture-model'],
          chat_model: 'fixture-model',
        },
      };
      return route.fulfill({ json: responses[url.pathname] || {} });
    }
    const candidate = path.resolve(dist, url.pathname.replace(/^\/+/, ''));
    const file =
      candidate.startsWith(dist + path.sep) && path.extname(candidate)
        ? candidate
        : path.join(dist, 'index.html');
    const types = {
      '.html': 'text/html',
      '.js': 'text/javascript',
      '.css': 'text/css',
      '.png': 'image/png',
    };
    try {
      return route.fulfill({
        body: await fs.readFile(file),
        contentType: types[path.extname(file)] || 'application/octet-stream',
      });
    } catch {
      return route.fulfill({ status: 404 });
    }
  });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.on('pageerror', (error) => errors.push(error.message));
  const dialog = page.getByRole('dialog', { name: 'Settings', exact: true });
  await page.goto('https://vela.test/ask');
  const composer = page.locator('textarea');
  await composer.fill('Keep this unfinished question');
  const opener = page.locator('.rail').getByRole('button', { name: 'Settings', exact: true });
  await opener.click();
  await dialog.waitFor();
  assert.equal(new URL(page.url()).pathname, '/ask');
  await dialog.getByRole('button', { name: 'Dark', exact: true }).click();
  await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
  await dialog.getByRole('button', { name: 'Done', exact: true }).waitFor();
  await page.screenshot({ path: path.join(shots, 'desktop-dark.png') });
  await dialog.getByRole('button', { name: 'Light', exact: true }).click();
  await page.waitForFunction(() => document.documentElement.dataset.theme === 'light');
  await page.screenshot({ path: path.join(shots, 'desktop-light.png') });
  await dialog.getByRole('button', { name: 'Notifications', exact: true }).click();
  await dialog.getByLabel('Topic', { exact: true }).fill('unsaved-fixture-topic');
  await dialog.getByRole('button', { name: 'Backups & storage', exact: true }).click();
  await dialog.getByRole('button', { name: 'Notifications', exact: true }).click();
  assert.equal(
    await dialog.getByLabel('Topic', { exact: true }).inputValue(),
    'unsaved-fixture-topic',
  );
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await dialog.getByText('Notification settings saved.', { exact: true }).waitFor();
  assert.equal(settings.ntfy_config.topic, 'unsaved-fixture-topic');
  await dialog.getByRole('button', { name: 'Chat & privacy' }).click();
  await dialog.getByRole('button', { name: 'Off', exact: true }).click();
  await page.waitForFunction(() => !localStorage.getItem('vela-chat'));
  await dialog.getByRole('button', { name: 'Done', exact: true }).click();
  assert.equal(settings.chat_history, false);
  assert.equal(await composer.inputValue(), 'Keep this unfinished question');
  assert.equal(await opener.evaluate((el) => el === document.activeElement), true);
  await opener.click();
  const search = dialog.getByRole('searchbox', { name: 'Find a setting' });
  await search.fill('ollama');
  assert.equal(await dialog.getByRole('navigation').getByRole('button').count(), 1);
  await search.fill('nothingmatches');
  await dialog.getByText('No settings found.').waitFor();
  await search.fill('');
  for (let i = 0; i < 17; i++) {
    await page.keyboard.press('Tab');
    // Native dialogs allow a tab stop in browser chrome (body is then active),
    // but never allow focus into the inert dashboard beneath the popup.
    assert.equal(
      await dialog.evaluate(
        (el) => el.contains(document.activeElement) || document.activeElement === document.body,
      ),
      true,
    );
  }
  failSave = true;
  await dialog.getByRole('button', { name: 'Dark', exact: true }).click();
  await dialog.getByRole('alert').getByText('Fixture save failure').waitFor();
  assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
  failSave = false;
  await page.keyboard.press('Escape');
  await dialog.waitFor({ state: 'detached' });
  await opener.click();
  await page.mouse.click(5, 5);
  await dialog.waitFor({ state: 'detached' });

  // Old bookmarks, search shortcuts, small screens, and both themes.
  await page.goto('https://vela.test/settings#backups');
  await dialog.getByRole('button', { name: 'Create backup' }).waitFor();
  await dialog.getByRole('button', { name: 'Done', exact: true }).click();
  assert.equal(new URL(page.url()).pathname, '/');
  await page.getByRole('searchbox', { name: 'Search', exact: true }).fill('storage');
  await page.getByRole('button', { name: 'Backups & storage Settings' }).click();
  await dialog.getByText('2.0 KB', { exact: true }).waitFor();
  await dialog.getByRole('button', { name: 'Done', exact: true }).click();

  // Developer tools: off by default, reversible, and presentation only. The
  // data directory is one of the facts it reveals.
  await page.goto('https://vela.test/ask');
  const draft = page.locator('textarea');
  await draft.fill('Draft that must survive the switch');
  assert.equal(await page.evaluate(() => localStorage.getItem('vela-developer-tools')), null);
  // The rail has no secondary "More" menu; Settings opens from the foot.
  assert.equal(await page.locator('.rail').getByRole('button', { name: 'More' }).count(), 0);
  await page.locator('.rail').getByRole('button', { name: 'Settings', exact: true }).click();
  await dialog.waitFor();
  assert.equal(
    await dialog.getByRole('button', { name: 'Developer tools', exact: true }).count(),
    0,
    'Developer tools is not a category until it is switched on',
  );
  await dialog
    .getByRole('navigation')
    .getByRole('button', { name: 'General', exact: true })
    .click();
  // The two names. They save together on Save names, not on every keystroke,
  // and the button stays disabled until something actually changed.
  const saveNames = dialog.getByRole('button', { name: 'Save names', exact: true });
  assert.equal(await saveNames.isDisabled(), true, 'nothing to save yet');
  await dialog.getByLabel('Your name', { exact: true }).fill('Marco');
  await dialog.getByLabel('Server name', { exact: true }).fill('vela.marco.house');
  assert.equal(await saveNames.isDisabled(), false);
  await saveNames.click();
  await page.waitForFunction(async () => {
    const response = await fetch('/api/settings');
    if (!response.ok) return false;
    const stored = (await response.json()).identity || {};
    return stored.displayName === 'Marco' && stored.initial === 'M';
  });
  // The rail draws the letter as soon as the names are saved, not on its next
  // poll, and names who it belongs to.
  const railAvatar = page.locator('.rail-avatar-item');
  await railAvatar.waitFor();
  assert.equal(await railAvatar.locator('.rail-avatar').innerText(), 'M');
  assert.match(await railAvatar.getAttribute('aria-label'), /Marco · vela\.marco\.house/);
  assert.equal(await saveNames.isDisabled(), true, 'saved, so nothing left to save');

  const devSwitch = dialog.getByRole('group', { name: 'Show developer tools' });
  assert.equal(
    await devSwitch.getByRole('button', { name: 'Off' }).getAttribute('aria-pressed'),
    'true',
  );
  await devSwitch.getByRole('button', { name: 'On', exact: true }).click();
  await dialog.getByRole('navigation').getByRole('button', { name: 'Developer tools' }).click();
  await dialog.getByText('/fixture/data', { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => localStorage.getItem('vela-developer-tools')), 'on');
  // Nothing beneath the popup reloaded, remounted or lost its draft.
  await dialog.getByRole('button', { name: 'Done', exact: true }).click();
  assert.equal(await draft.inputValue(), 'Draft that must survive the switch');
  assert.equal(new URL(page.url()).pathname, '/ask');

  // The choice survives a reload, and another tab on the same origin follows.
  await page.reload();
  await page.locator('.rail').getByRole('button', { name: 'Settings', exact: true }).click();
  await dialog.getByRole('navigation').getByRole('button', { name: 'Developer tools' }).click();
  await dialog.getByText('/fixture/data', { exact: true }).waitFor();
  const second = await context.newPage();
  await second.goto('https://vela.test/');
  await second.locator('.rail').waitFor();
  // A real write from the other tab, delivered as a storage event.
  await second.evaluate(() => localStorage.setItem('vela-developer-tools', 'off'));
  await dialog
    .getByRole('navigation')
    .getByRole('button', { name: 'Developer tools' })
    .waitFor({ state: 'detached' });
  // The panel that was open explains itself and offers the switch back.
  await dialog.getByRole('heading', { name: 'Developer tools are off' }).waitFor();
  await dialog.getByRole('button', { name: 'Back to General' }).click();
  await dialog.getByRole('button', { name: 'Done', exact: true }).click();
  await second.close();

  // A browser that refuses to store this preference still applies the choice
  // for the session and says that it will not last. Only this one key is
  // denied, so the rest of the dashboard behaves normally.
  const sealed = await context.newPage();
  await sealed.addInitScript(() => {
    const { getItem, setItem } = Storage.prototype;
    Storage.prototype.getItem = function (key) {
      if (key === 'vela-developer-tools') throw new Error('storage denied');
      return getItem.call(this, key);
    };
    Storage.prototype.setItem = function (key, value) {
      if (key === 'vela-developer-tools') throw new Error('storage denied');
      return setItem.call(this, key, value);
    };
  });
  await sealed.goto('https://vela.test/');
  await sealed.locator('.rail').getByRole('button', { name: 'Settings', exact: true }).click();
  const sealedDialog = sealed.getByRole('dialog', { name: 'Settings', exact: true });
  await sealedDialog
    .getByRole('navigation')
    .getByRole('button', { name: 'General', exact: true })
    .click();
  await sealedDialog
    .getByRole('group', { name: 'Show developer tools' })
    .getByRole('button', { name: 'On', exact: true })
    .click();
  await sealedDialog
    .getByRole('navigation')
    .getByRole('button', { name: 'Developer tools' })
    .waitFor();
  await sealedDialog.getByText(/not storing preferences/).waitFor();
  await sealed.close();
  // ---- A phone: Settings is a screen, not a popup. ----
  await page.goto('https://vela.test/ask');
  const phoneDraft = page.locator('textarea');
  await phoneDraft.fill('Draft that must survive the phone screens');
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 700 });
    // The rail stays beside Ask on a phone, so Settings is reached from it; the
    // unsent question stays mounted behind the whole journey.
    await page.locator('.rail').getByRole('button', { name: 'Settings', exact: true }).click();
    await dialog.waitFor();
    // Ordinary entry lands on the category list, and the list covers the
    // workspace edge to edge with no popup gap around it.
    const listHeading = dialog.getByRole('heading', { name: 'Settings', level: 1 });
    await listHeading.waitFor();
    const edgeToEdge = await dialog.evaluate((el) => {
      const box = el.getBoundingClientRect();
      return box.left <= 0 && box.right >= innerWidth && box.height >= innerHeight - 1;
    });
    assert.ok(edgeToEdge, `Settings is not edge to edge at ${width}px`);
    assert.equal(await dialog.getByRole('button', { name: 'Done', exact: true }).count(), 0);

    for (const theme of ['light', 'dark']) {
      // One section at a time, reached from the list and left with Back.
      for (const section of ['Notifications', 'General', 'Appearance', 'Security']) {
        await dialog
          .getByRole('navigation')
          .getByRole('button', { name: new RegExp(`^${section}`) })
          .click();
        await dialog
          .locator('.settings-header')
          .getByRole('heading', { name: section, level: 2 })
          .waitFor();
        const fits = await dialog.evaluate((el) => {
          const bounds = el.getBoundingClientRect();
          const content = el.querySelector('.settings-content');
          const header = el.querySelector('.settings-header');
          return {
            box: bounds.left >= 0 && bounds.right <= innerWidth && bounds.bottom <= innerHeight,
            scroll: content.scrollWidth - content.clientWidth,
            targets: [...header.querySelectorAll('button')].map(
              (button) => button.getBoundingClientRect().height,
            ),
          };
        });
        assert.ok(fits.box, `${section} overflows the screen at ${width}px`);
        assert.ok(
          fits.scroll <= 0,
          `${section} scrolls sideways by ${fits.scroll}px at ${width}px`,
        );
        assert.ok(
          fits.targets.every((height) => height >= 44),
          `${section} header targets are ${fits.targets.join('/')}px at ${width}px`,
        );
        if (section === 'Appearance') {
          await dialog
            .getByRole('button', { name: theme === 'dark' ? 'Dark' : 'Light', exact: true })
            .click();
          await page.waitForFunction((t) => document.documentElement.dataset.theme === t, theme);
          await page.screenshot({ path: path.join(shots, `phone-${width}-${theme}.png`) });
        }
        if (section === 'Security')
          await page.screenshot({ path: path.join(shots, `phone-security-${width}-${theme}.png`) });
        await dialog.getByRole('button', { name: 'Back to Settings' }).click();
        await listHeading.waitFor();
      }
    }

    // Search still narrows the list, and a search result opens its section.
    const phoneSearch = dialog.getByRole('searchbox', { name: 'Find a setting' });
    await phoneSearch.fill('pin');
    assert.equal(await dialog.getByRole('navigation').getByRole('button').count(), 1);
    await phoneSearch.fill('');

    // Escape follows the same visible hierarchy as Back: section, list, out.
    await dialog
      .getByRole('navigation')
      .getByRole('button', { name: /^Appearance/ })
      .click();
    await page.keyboard.press('Escape');
    await listHeading.waitFor();
    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'detached' });
    // Nothing beneath the screen reloaded or lost its draft.
    assert.equal(await phoneDraft.inputValue(), 'Draft that must survive the phone screens');
  }

  // ---- Every supported width, both compositions, and the line between. ----
  await page.goto('https://vela.test/ask');
  const crossing = page.locator('textarea');
  await crossing.fill('A draft that must survive the crossover');
  await page.setViewportSize({ width: 390, height: 780 });
  await page.locator('.rail').getByRole('button', { name: 'Settings', exact: true }).click();
  await dialog.waitFor();
  await dialog
    .getByRole('navigation')
    .getByRole('button', { name: /^Notifications/ })
    .click();
  await dialog.getByLabel('Topic', { exact: true }).fill('crossover-fixture-topic');
  for (const [width, height] of [
    [320, 700],
    [430, 900],
    [768, 1024],
    [860, 800],
    [740, 380],
    [861, 800],
    [1440, 900],
  ]) {
    await page.setViewportSize({ width, height });
    const shape = await dialog.evaluate((el) => {
      const box = el.getBoundingClientRect();
      const content = el.querySelector('.settings-content');
      return {
        screen: el.classList.contains('settings-screen'),
        left: box.left,
        right: box.right,
        bottom: box.bottom,
        sideways: content.scrollWidth - content.clientWidth,
      };
    });
    // Through the compact threshold it is a screen; above it, a popup.
    assert.equal(shape.screen, width <= 860, `wrong composition at ${width}px`);
    if (shape.screen)
      assert.ok(
        shape.left <= 0 && shape.right >= width,
        `the screen leaves a gap at ${width}px: ${JSON.stringify(shape)}`,
      );
    else assert.ok(shape.left > 0, `the popup lost its margin at ${width}px`);
    assert.ok(shape.bottom <= height + 1, `overflows the window at ${width}x${height}`);
    assert.ok(shape.sideways <= 0, `scrolls sideways at ${width}px`);
    // The draft survives every one of those, including the crossover itself.
    assert.equal(
      await dialog.getByLabel('Topic', { exact: true }).inputValue(),
      'crossover-fixture-topic',
      `the draft was lost at ${width}px`,
    );
  }

  // A reader at 200% zoom has a genuinely narrow layout viewport, so they get
  // the same screen composition rather than a popup squeezed into it.
  await page.setViewportSize({ width: 1440, height: 900 });
  const zoomed = await context.newPage();
  await zoomed.goto('https://vela.test/');
  await zoomed.setViewportSize({ width: 720, height: 450 });
  await zoomed.locator('.rail').getByRole('button', { name: 'Settings', exact: true }).click();
  const zoomedDialog = zoomed.getByRole('dialog', { name: 'Settings', exact: true });
  await zoomedDialog.waitFor();
  assert.ok(await zoomedDialog.evaluate((el) => el.classList.contains('settings-screen')));
  await zoomed.close();

  // Reduced motion removes the screen transition rather than shortening it.
  const still = await context.newPage();
  await still.emulateMedia({ reducedMotion: 'reduce' });
  await still.setViewportSize({ width: 390, height: 780 });
  await still.goto('https://vela.test/');
  await still.locator('.rail').getByRole('button', { name: 'Settings', exact: true }).click();
  const stillDialog = still.getByRole('dialog', { name: 'Settings', exact: true });
  await stillDialog
    .getByRole('navigation')
    .getByRole('button', { name: /^Appearance/ })
    .click();
  assert.equal(
    await stillDialog
      .locator('.settings-main')
      .evaluate((el) => getComputedStyle(el).animationName),
    'none',
  );

  // An open keyboard shrinks the scrolling body, not the header. A headless
  // browser has no keyboard, so the measurement service's own variables stand
  // in for one; the layout rules that read them are the real ones.
  await still.evaluate(() => {
    // The measurement service publishes these as inline custom properties, so
    // the stand-in writes them the same way.
    document.documentElement.style.setProperty('--vela-visible-height', '420px');
    document.documentElement.style.setProperty('--vela-keyboard-inset', '360px');
  });
  const withKeyboard = await stillDialog.evaluate((el) => {
    const box = el.getBoundingClientRect();
    const header = el.querySelector('.settings-header').getBoundingClientRect();
    const content = el.querySelector('.settings-content');
    return {
      height: box.height,
      top: box.top,
      headerTop: header.top,
      contentScrolls: content.scrollHeight > content.clientHeight || content.clientHeight > 0,
    };
  });
  assert.ok(
    Math.abs(withKeyboard.height - 420) <= 1 && withKeyboard.top <= 1,
    `the screen must own the visible rectangle: ${JSON.stringify(withKeyboard)}`,
  );
  assert.ok(
    withKeyboard.headerTop >= -1 && withKeyboard.headerTop < 60,
    'the header is pushed off the top when the keyboard opens',
  );
  await still.close();

  // Updates: the copy has to say exactly what leaves this computer, and the
  // switch beside it has to stop the request entirely.
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.goto('https://vela.test/');
  await page.goto('https://vela.test/settings#updates');
  await dialog.locator('#settings-updates').waitFor();
  await dialog.getByText('one anonymous request to github.com', { exact: false }).waitFor();
  await dialog.getByText('no identifier', { exact: false }).waitFor();
  await dialog.getByText('Vela has not checked yet.').waitFor();
  assert.equal(updateChecks, 0, 'opening Updates must not check');

  // Checking on by default, installing automatically opt-in.
  const checkSwitch = dialog.getByRole('switch', { name: 'Check for new versions' });
  assert.equal(await checkSwitch.getAttribute('aria-checked'), 'true');

  await dialog.getByRole('button', { name: 'Check now' }).click();
  await dialog.getByRole('heading', { name: 'Vela 0.2.0 is available' }).waitFor();
  assert.equal(updateChecks, 1);
  await dialog.getByText('This is the portable Windows folder.').waitFor();

  // Release notes render, with images dropped and links opening elsewhere.
  await dialog.getByRole('heading', { name: 'Release notes' }).waitFor();
  await dialog.getByText('A new desk').waitFor();
  assert.equal(await dialog.locator('.update-notes img').count(), 0, 'images are stripped');
  assert.equal(await dialog.locator('.update-notes a').first().getAttribute('target'), '_blank');
  await page.screenshot({ path: path.join(shots, 'settings-updates.png') });

  // Automatic installing is its own choice, and starts off.
  const modeGroup = dialog.getByRole('group', { name: 'When an update is available' });
  await modeGroup.waitFor();
  assert.equal(
    await modeGroup.getByRole('button', { name: 'Tell me' }).getAttribute('aria-pressed'),
    'true',
  );

  // Installing is offered where Vela can actually do it, and says what will
  // happen before it starts.
  await dialog.getByRole('button', { name: 'Update now' }).click();
  await page.getByRole('heading', { name: 'Install Vela 0.2.0?' }).waitFor();
  await page.getByText('check it against its published checksum', { exact: false }).waitFor();
  assert.equal(applied, false, 'the dialog must not install anything by opening');
  await page.screenshot({ path: path.join(shots, 'settings-update-confirm.png') });

  await page.getByRole('button', { name: 'Install it' }).click();
  // The overlay takes over: there is nothing to do but wait.
  await page.locator('.update-overlay').waitFor();
  assert.equal(applied, true);
  await page.getByText('Downloading…').waitFor();
  assert.equal(
    await page.locator('.update-progress [role], .update-progress').first().isVisible(),
    true,
  );
  await page.screenshot({ path: path.join(shots, 'settings-update-progress.png') });

  // Put the fixture back so the rest of the suite sees a settled section.
  updateJob = { state: 'idle', percent: 0, message: '', rollback: false };
  await page.goto('https://vela.test/');
  await page.goto('https://vela.test/settings#updates');
  await dialog.locator('#settings-updates').waitFor();

  // Turning the check off disables the button that would make the request.
  await checkSwitch.click();
  await page.waitForFunction(
    () =>
      document.querySelector('#settings-updates button[aria-busy], #settings-updates') &&
      !document
        .querySelector('#settings-updates')
        .querySelector('[role="switch"]')
        .getAttribute('aria-checked')
        .includes('true'),
  );
  assert.equal(await dialog.getByRole('button', { name: 'Check now' }).isDisabled(), true);
  assert.equal(updateChecks, 1, 'turning it off must not check');

  await page.goto('https://vela.test/');

  // Backups: what is protected, the schedule, and a restore that asks for the
  // backup's name before it replaces anything.
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.goto('https://vela.test/');
  await page.goto('https://vela.test/settings#backups');
  await dialog.locator('#settings-backups').waitFor();
  await dialog.getByText('Not scheduled').waitFor();
  await dialog.getByRole('button', { name: 'Create backup' }).waitFor();

  // The schedule is off until someone turns it on, and then says when.
  const scheduleSwitch = dialog.getByRole('switch', { name: 'Back up automatically' });
  assert.equal(await scheduleSwitch.getAttribute('aria-checked'), 'false');
  assert.equal(await dialog.getByLabel('Backups to keep').count(), 0);
  await scheduleSwitch.click();
  await dialog.getByLabel('Backups to keep').waitFor();
  assert.equal(await scheduleSwitch.getAttribute('aria-checked'), 'true');
  await dialog.getByLabel('Backups to keep').fill('4');
  await dialog.getByLabel('Backups to keep').blur();
  await page.waitForFunction(() => !document.body.innerText.includes('Not scheduled'), undefined, {
    timeout: 5000,
  });
  await page.screenshot({ path: path.join(shots, 'settings-backups.png') });

  // Restore asks first, in full, and will not act until the name is typed.
  await dialog.getByRole('button', { name: 'Restore' }).first().click();
  const restoreDrawer = page.getByRole('dialog', { name: 'Restore a backup' });
  await restoreDrawer.waitFor();
  await restoreDrawer.getByText('everything your apps saved').waitFor();
  const confirmButton = restoreDrawer.getByRole('button', { name: 'Restore this backup' });
  assert.equal(await confirmButton.isDisabled(), true, 'the name must be typed first');
  await restoreDrawer.getByRole('textbox').fill('not-the-name');
  assert.equal(await confirmButton.isDisabled(), true, 'the wrong name must not enable it');
  await page.screenshot({ path: path.join(shots, 'settings-restore.png') });
  await restoreDrawer.getByRole('textbox').fill('20260916-030000');
  assert.equal(await confirmButton.isDisabled(), false);
  await confirmButton.click();
  await restoreDrawer.waitFor({ state: 'detached' });
  assert.equal(restored, '20260916-030000');
  // It says where the copy of what it replaced went.
  await dialog
    .getByText(/pre-restore-20260916-094500/)
    .first()
    .waitFor();
  // And that copy is listed, marked as one Vela took rather than one you made.
  await dialog.getByText('taken before a restore').waitFor();

  await page.goto('https://vela.test/');

  // Health: opening the section must not start a sweep — thirteen checks
  // should not run because a popup opened. Run now is the deliberate action.
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.goto('https://vela.test/');
  await page.goto('https://vela.test/settings#health');
  // The section heading, not the popup's own header for the category.
  await dialog.locator('#settings-health').getByRole('heading', { name: 'Health' }).waitFor();
  await dialog.getByText('Vela has not checked itself yet.').waitFor();
  assert.equal(doctorRuns, 0, 'opening Health must not run the checks');
  assert.equal(await dialog.locator('.health-row').count(), 0);

  await dialog.getByRole('button', { name: 'Run now' }).click();
  await dialog.locator('.health-row').first().waitFor();
  assert.equal(doctorRuns, 1);
  // The failing check leads, a skipped one is summarised rather than listed.
  assert.deepEqual(await dialog.locator('.health-title').allInnerTexts(), [
    'App state',
    'Room to work',
  ]);
  assert.equal(await dialog.locator('.health-row-fail').count(), 1);
  await dialog.getByText('1 of 2 checks need attention.').waitFor();
  await dialog.getByText('1 check did not apply to this server and was skipped.').waitFor();
  await page.screenshot({ path: path.join(shots, 'settings-health.png') });

  // Repair fixes the row it was pressed on, without a second sweep.
  assert.equal(await dialog.getByRole('button', { name: 'Repair' }).count(), 1);
  await dialog.getByRole('button', { name: 'Repair' }).click();
  await dialog.getByText('Every app Vela lists as running really is.').waitFor();
  assert.equal(await dialog.locator('.health-row-fail').count(), 0);
  assert.equal(await dialog.getByRole('button', { name: 'Repair' }).count(), 0);
  assert.equal(doctorRuns, 1, 'a repair must not trigger a whole sweep');

  // Back to the desk, so the next deep link is a real navigation rather than a
  // hash change on the document already open.
  await page.goto('https://vela.test/');

  // A section asked for by name still opens directly, and the wide window
  // keeps the two-pane popup with its Done footer.
  await page.setViewportSize({ width: 390, height: 700 });
  await page.goto('https://vela.test/settings#backups');
  await dialog.getByRole('button', { name: 'Create backup' }).waitFor();
  await dialog.getByRole('button', { name: 'Back to Settings' }).click();
  await dialog.getByRole('heading', { name: 'Settings', level: 1 }).waitFor();
  await page.setViewportSize({ width: 1366, height: 900 });
  await dialog.getByRole('button', { name: 'Done', exact: true }).waitFor();
  await dialog.getByRole('button', { name: 'Done', exact: true }).click();
  assert.deepEqual(errors, []);
  console.log(
    'PASS: settings popup, the update check with its privacy copy and switch, the backup schedule and a confirmed restore, Health checks with Run now and Repair, page/draft preservation, saves and rollback, category search, focus containment/restoration, Escape/backdrop, deep links, the developer-tools preference across reloads/tabs/denied storage, the phone screens with Back and Escape, and 320/390/430/768/860/861/1440, short landscape, 200% zoom, reduced motion and an open keyboard keeping one draft',
  );
} finally {
  await browser.close();
}
