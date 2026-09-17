import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const python =
  process.env.VELA_TEST_PYTHON ||
  path.join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
// This server creates disposable data and a pinned fixture catalog.
const server = spawn(python, ['scripts/serve-release-fixtures.py'], {
  cwd: root,
  windowsHide: true,
  stdio: ['ignore', 'pipe', 'pipe'],
});
const base = 'http://127.0.0.1:17715';
let output = '',
  browser;
server.stderr.on('data', (data) => {
  output += data;
});
server.on('error', (error) => {
  output += error.message;
});
// Where each destination lives: `rail` is a shortcut beside the workspace,
// `more` is the labelled secondary menu, `popup` opens over the current page.
// All apps is an overlay like Settings — it draws over the page rather than
// replacing it. System is absent entirely until Developer tools is on, and is
// checked separately below.
const pages = [
  ['/', 'Desk', 'rail'],
  ['/ask', 'Ask', 'rail'],
  ['/apps', 'All apps', 'overlay'],
  ['/library', 'Marketplace', 'rail'],
  ['/automations', 'Automations', 'none'],
  ['/settings', 'Settings', 'popup'],
];
try {
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw new Error(output);
    try {
      if ((await fetch(base + '/api/health')).ok) break;
    } catch {}
    if (i === 99) throw new Error(`Fixture server did not start: ${output}`);
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  browser = await chromium.launch({
    headless: true,
    channel: process.env.VELA_BROWSER_CHANNEL || 'chrome',
  });
  const page = await browser.newPage();
  // Onboarding has a dedicated suite; this suite checks the dashboard beneath it.
  await page.addInitScript(() => localStorage.setItem('vela.welcome.v1', 'done'));
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const shots = path.join(root, 'docs/screenshots/shared-foundations');
  await fs.mkdir(shots, { recursive: true });
  const originalCss = process.env.VELA_BASELINE_CSS
    ? await fs.readFile(process.env.VELA_BASELINE_CSS, 'utf8')
    : null;
  for (const viewport of [
    { width: 1366, height: 900 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    for (const theme of ['light', 'dark']) {
      for (const [route, label, where] of pages) {
        await page.goto(base + route);
        await page.locator('.rail a').first().waitFor({ state: 'attached' });
        // A healthy server says nothing; the only connection element is the
        // failure notice, which must not be present against this fixture.
        assert.equal(await page.locator('.connection-alert').count(), 0);
        await page.evaluate(async (theme) => {
          document.documentElement.dataset.theme = theme;
          await document.fonts.ready;
        }, theme);
        await page.addStyleTag({
          content:
            '*, *::before, *::after { animation: none !important; transition: none !important; }',
        });
        // Every workspace keeps the rail on screen at every width.
        if (where === 'popup') {
          await page.getByRole('dialog', { name: 'Settings', exact: true }).waitFor();
        } else if (where === 'overlay') {
          // The grid is over the page, and the rail underneath it is still the
          // way out.
          await page.locator('.apps-overlay .launchpad').waitFor();
          await page.locator('.rail').waitFor();
        } else {
          assert.equal(await page.getByRole('button', { name: 'Open navigation' }).count(), 0);
          const nav = page.locator('.rail');
          await nav.waitFor();
          // Desk is fixed at the top and All apps sits beside it as a button,
          // because it opens over the page. Ask and the Library are the default
          // pins. There is no secondary menu. Settings closes the set.
          assert.equal(await nav.locator('.rail-group a').count(), 1);
          assert.equal(await nav.locator('.rail-group button').count(), 1);
          assert.equal(await nav.locator('.rail-foot button').count(), 1);
          assert.equal(await nav.getByRole('button', { name: 'More', exact: true }).count(), 0);
          if (where === 'rail') {
            assert.equal(
              await nav
                .getByRole('link', { name: label, exact: label !== 'Library' })
                .getAttribute('aria-current'),
              'page',
            );
          } else {
            // Automations is reachable from the Launchpad and search, not the
            // rail, so no rail item is marked while it is open.
            assert.equal(await nav.locator('a[aria-current="page"]').count(), 0);
          }
        }
        const overflow = await page.evaluate(() => {
          const main = document.querySelector('.workspace-content');
          return {
            body: document.documentElement.scrollWidth - innerWidth,
            main: main.scrollWidth - main.clientWidth,
          };
        });
        assert.ok(
          overflow.body <= 1 && overflow.main <= 1,
          `${label} ${theme} ${viewport.width}: ${JSON.stringify(overflow)}`,
        );
        if (originalCss) {
          // Reapply original declarations to the same DOM after the compiled
          // styles. All computed properties and geometry should remain identical.
          const differences = await page.evaluate((css) => {
            const elements = [...document.querySelectorAll('.shell, .shell *')];
            const snapshot = () =>
              elements.map((element) => {
                const style = getComputedStyle(element);
                return Object.fromEntries(
                  [...style]
                    .filter((name) => !name.startsWith('--'))
                    .map((name) => [name, style.getPropertyValue(name)]),
                );
              });
            const before = snapshot();
            const original = document.createElement('style');
            original.textContent = css.replace(/@import[^;]+;/g, '');
            document.head.append(original);
            const after = snapshot();
            const differences = [];
            for (let i = 0; i < elements.length; i++) {
              for (const property of Object.keys(before[i])) {
                if (before[i][property] !== after[i][property])
                  differences.push({
                    element: elements[i].className,
                    property,
                    before: before[i][property],
                    after: after[i][property],
                  });
              }
            }
            original.remove();
            return differences.slice(0, 10);
          }, originalCss);
          assert.deepEqual(differences, [], `CSS changed: ${route} ${theme} ${viewport.width}`);
        }
        await page.screenshot({
          path: path.join(
            shots,
            `${label.toLowerCase().replace(/ /g, '-')}-${theme}-${viewport.width}.png`,
          ),
        });
      }
    }
  }
  // The plan's viewport matrix, checked for overflow and reachable navigation.
  // 1366 and 390 already have full screenshot coverage above.
  for (const size of [
    { width: 320, height: 720 },
    { width: 768, height: 1024 },
    { width: 1024, height: 768 },
    { width: 1920, height: 1080 },
    { width: 900, height: 420 },
  ]) {
    await page.setViewportSize(size);
    for (const [route, label, where] of pages) {
      if (where === 'popup') continue;
      await page.goto(base + route);
      // The rail is the navigation at every width, on every page.
      const selector = '.rail';
      await page.locator(selector).waitFor();
      const box = await page.evaluate((selector) => {
        const content = document.querySelector('.workspace-content');
        const rect = document.querySelector(selector).getBoundingClientRect();
        return {
          body: document.documentElement.scrollWidth - innerWidth,
          content: content.scrollWidth - content.clientWidth,
          navVisible: rect.width > 0 && rect.height > 0,
          navBottom: rect.bottom,
          height: innerHeight,
        };
      }, selector);
      const at = `${label} ${size.width}x${size.height}`;
      assert.ok(box.body <= 1, `${at} body overflow: ${JSON.stringify(box)}`);
      assert.ok(box.content <= 1, `${at} content overflow: ${JSON.stringify(box)}`);
      assert.ok(box.navVisible, `${at} navigation must stay visible`);
      assert.ok(
        box.navBottom <= box.height + 1,
        `${at} navigation must fit: ${JSON.stringify(box)}`,
      );
    }
  }

  // The desk itself: the seeded board renders host widgets over the wallpaper,
  // the board narrows to the phone layout below 860px, and the absolutely
  // positioned frames never push the content sideways at any width.
  for (const size of [
    { width: 1366, height: 900 },
    { width: 1024, height: 768 },
    { width: 390, height: 844 },
    { width: 320, height: 720 },
  ]) {
    await page.setViewportSize(size);
    await page.goto(base + '/');
    const grid = page.locator('.desk-grid');
    await grid.waitFor();
    const phone = size.width <= 860;
    const names = phone
      ? ['Clock', 'Needs you', 'Your apps', 'Ask']
      : ['Clock', 'Your apps', 'Running now', 'Needs you', 'Ask'];
    for (const name of names) {
      await page.getByRole('region', { name, exact: true }).waitFor();
    }
    assert.equal(
      await grid.locator('.desk-frame').count(),
      names.length,
      `the ${phone ? 'phone' : 'desktop'} board at ${size.width}px`,
    );
    const box = await page.evaluate(() => {
      const host = document.querySelector('.desk-grid');
      const frames = [...host.querySelectorAll('.desk-frame')].map((frame) => {
        const rect = frame.getBoundingClientRect();
        return rect.right - host.getBoundingClientRect().right;
      });
      return {
        grid: host.scrollWidth - host.clientWidth,
        past: Math.max(...frames),
        wallpaper: getComputedStyle(document.querySelector('.shell'), '::before').backgroundImage,
      };
    });
    assert.ok(box.grid <= 1, `desk grid overflow at ${size.width}: ${JSON.stringify(box)}`);
    assert.ok(
      box.past <= 1,
      `a widget hangs past the board at ${size.width}: ${JSON.stringify(box)}`,
    );
    assert.ok(box.wallpaper.includes('wallpapers/choroni.jpg'), `desk wallpaper: ${box.wallpaper}`);
  }
  // Leaving the desk takes the wallpaper with it.
  await page.goto(base + '/library');
  await page.locator('.rail').waitFor();
  assert.equal(await page.evaluate(() => document.body.dataset.desk ?? null), null);

  // The desk's navigation survives a fresh browser, a reload, Back, a rotation
  // and a scrolled page, and it never sits on top of the content it navigates.
  for (const size of [
    { width: 390, height: 844 },
    { width: 844, height: 390 },
    { width: 320, height: 720 },
  ]) {
    await page.setViewportSize(size);
    await page.goto(base + '/');
    await page.locator('.rail').waitFor();
    assert.equal(
      await page.getByRole('button', { name: 'Open navigation' }).count(),
      0,
      `The desk must not need a hamburger at ${size.width}px`,
    );
    await page.reload();
    await page.locator('.rail').waitFor();
    await page.locator('.rail a[href="/library"]').click();
    await page.waitForURL(`${base}/library`);
    await page.goBack();
    await page.waitForURL(`${base}/`);
    const geometry = await page.evaluate(() => {
      const content = document.querySelector('.workspace-content');
      content.scrollTop = content.scrollHeight;
      const rail = document.querySelector('.rail').getBoundingClientRect();
      const main = document.querySelector('.workspace-main').getBoundingClientRect();
      return { railRight: rail.right, mainLeft: main.left, railWidth: rail.width };
    });
    assert.ok(geometry.railWidth > 0, `rail hidden at ${size.width}x${size.height}`);
    assert.ok(
      geometry.railRight <= geometry.mainLeft + 1,
      `the rail must not cover the desk at ${size.width}x${size.height}: ${JSON.stringify(geometry)}`,
    );
    await page.locator('.rail').waitFor();
  }

  // Real page interactions using the shared header, fields, and empty state.
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.goto(base + '/library');
  await page.getByRole('searchbox', { name: 'Search the Marketplace' }).fill('no-such-fixture');
  await page.getByRole('heading', { name: 'No matches', exact: true }).waitFor();
  await page.getByRole('searchbox', { name: 'Search the Marketplace' }).fill('');

  // The Marketplace's Installed tab is a real URL, reached directly.
  await page.goto(base + '/library?tab=installed');
  assert.equal(
    await page.getByRole('tab', { name: 'Installed' }).getAttribute('aria-selected'),
    'true',
  );

  // Adding an app offers exactly the sources the server supports. A manifest
  // URL and pasted JSON are not supported and must not be advertised. The
  // server-folder source is a development tool and stays out of the way until
  // Developer tools is on.
  const opener = page.getByRole('button', { name: 'Add an app', exact: true });
  await opener.click();
  const addDialog = page.getByRole('dialog', { name: 'Add an app', exact: true });
  assert.equal(await addDialog.getByRole('radio').count(), 2);
  assert.equal(await addDialog.getByRole('radio', { name: /Folder on the server/ }).count(), 0);
  assert.equal(await addDialog.getByText(/vela\.json|Paste the JSON|From a URL/).count(), 0);
  await addDialog.getByRole('radio', { name: /Connect a website/ }).click();
  await addDialog.getByRole('button', { name: 'Set up a connection' }).waitFor();
  await addDialog.getByRole('radio', { name: /Install from file/ }).click();
  await addDialog.getByLabel('Install from file', { exact: true }).waitFor();
  await page.keyboard.press('Escape');
  await addDialog.waitFor({ state: 'detached' });
  assert.equal(
    await page.evaluate(() => document.activeElement.textContent),
    'Add an app',
    'closing the add dialog returns focus to its opener',
  );

  // The same flow has to fit and stay reachable on a phone.
  await page.setViewportSize({ width: 390, height: 844 });
  await opener.click();
  await addDialog.waitFor();
  const dialogBox = await addDialog.boundingBox();
  assert.ok(dialogBox.x >= 0 && dialogBox.x + dialogBox.width <= 390, JSON.stringify(dialogBox));
  await addDialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await addDialog.waitFor({ state: 'detached' });
  await page.setViewportSize({ width: 1366, height: 900 });

  // A direct link to a technical destination explains itself and offers the
  // switch. Visiting it must never turn the preference on by itself.
  await page.goto(base + '/environments');
  await page.getByRole('heading', { name: 'Developer tools are off' }).waitFor();
  assert.equal(await page.evaluate(() => localStorage.getItem('vela-developer-tools')), null);
  await page.getByRole('button', { name: 'Enable developer tools' }).click();
  await page.getByRole('heading', { name: 'Local Engine' }).waitFor();
  assert.equal(await page.evaluate(() => localStorage.getItem('vela-developer-tools')), 'on');
  // System becomes a searchable, pinnable core tool while developer tools are
  // on, and the folder source reappears. There is no secondary rail menu.
  assert.equal(
    await page.locator('.rail').getByRole('button', { name: 'More', exact: true }).count(),
    0,
  );
  await page.goto(base + '/library');
  await opener.click();
  await addDialog.waitFor();
  assert.equal(await addDialog.getByRole('radio').count(), 3);
  await addDialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.evaluate(() => localStorage.removeItem('vela-developer-tools'));

  // The desk's own settings: a volume must be a folder that exists, and the
  // server is the one that decides, so a bad path comes back as its refusal
  // rather than as something the page guessed.
  await page.goto(base + '/settings#desk');
  const desk = page.getByRole('dialog', { name: 'Settings', exact: true });
  await desk.getByRole('heading', { name: 'Volumes', exact: true }).waitFor();
  // Settings › Files has a Folder field of its own, so this one is reached
  // through the Desk panel rather than by label alone.
  const deskPanel = desk.locator('#settings-desk');
  await deskPanel.getByLabel('Folder', { exact: true }).fill('/definitely/not/a/folder');
  await deskPanel.getByRole('button', { name: 'Add volume', exact: true }).click();
  await deskPanel.getByRole('alert').filter({ hasText: 'not a folder on this computer' }).waitFor();
  await page.keyboard.press('Escape');

  // The endpoint the System and Volume widgets read answers for this machine.
  const metrics = await page.evaluate(async () => {
    const session = await fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' } });
    const { token } = await session.json();
    const response = await fetch('/api/system/metrics', {
      headers: { Authorization: `Bearer ${token}` },
    });
    return response.ok ? response.json() : { status: response.status };
  });
  // Whether this machine has psutil is not the point; that the desk's source
  // answers instead of erroring is. A server without it must say so in the
  // payload rather than in a status code.
  assert.equal(
    typeof metrics.available,
    'boolean',
    `system metrics must answer either way: ${JSON.stringify(metrics)}`,
  );

  await page.goto(base + '/settings#notifications');
  await page.getByLabel('Topic', { exact: true }).fill('local-fixture-topic');
  assert.equal(await page.getByLabel('Topic', { exact: true }).inputValue(), 'local-fixture-topic');
  assert.deepEqual(errors, []);
  console.log(
    'PASS: six default destinations, the desk board over its wallpaper, the secondary menu, desk navigation without a hamburger across reload/back/rotation/scroll, light/dark themes, 320/390/768/1024/1366/1920 and short-landscape layouts, library filtering, supported add-app sources, the developer-tools deep link, desk volume validation and field labels' +
      (originalCss ? '; computed styles match the original CSS' : ''),
  );
} finally {
  await browser?.close();
  server.kill();
  if (server.exitCode === null) await new Promise((resolve) => server.once('exit', resolve));
}
