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
          settings = { ...settings, ...patch };
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
        '/api/backups': { backups: [] },
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
  const opener = page.locator('.rail').getByRole('button', { name: 'Settings' });
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
  await page.locator('.rail').getByRole('button', { name: 'Settings' }).click();
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
  await page.locator('.rail').getByRole('button', { name: 'Settings' }).click();
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
  await sealed.locator('.rail').getByRole('button', { name: 'Settings' }).click();
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
    await page.locator('.rail').getByRole('button', { name: 'Settings' }).click();
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
  await page.locator('.rail').getByRole('button', { name: 'Settings' }).click();
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
  await zoomed.locator('.rail').getByRole('button', { name: 'Settings' }).click();
  const zoomedDialog = zoomed.getByRole('dialog', { name: 'Settings', exact: true });
  await zoomedDialog.waitFor();
  assert.ok(await zoomedDialog.evaluate((el) => el.classList.contains('settings-screen')));
  await zoomed.close();

  // Reduced motion removes the screen transition rather than shortening it.
  const still = await context.newPage();
  await still.emulateMedia({ reducedMotion: 'reduce' });
  await still.setViewportSize({ width: 390, height: 780 });
  await still.goto('https://vela.test/');
  await still.locator('.rail').getByRole('button', { name: 'Settings' }).click();
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
    'PASS: settings popup, Health checks with Run now and Repair, page/draft preservation, saves and rollback, category search, focus containment/restoration, Escape/backdrop, deep links, the developer-tools preference across reloads/tabs/denied storage, the phone screens with Back and Escape, and 320/390/430/768/860/861/1440, short landscape, 200% zoom, reduced motion and an open keyboard keeping one draft',
  );
} finally {
  await browser.close();
}
