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
const pages = [
  ['/', 'Home'],
  ['/apps', 'Apps'],
  ['/library', 'Library'],
  ['/ask', 'Ask'],
  ['/environments', 'System'],
  ['/automations', 'Automations'],
  ['/settings', 'Settings'],
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
      for (const [route, label] of pages) {
        await page.goto(base + route);
        await page.locator('.rail a').first().waitFor({ state: 'attached' });
        await page.waitForFunction(() => document.querySelector('.host-badge-ok'));
        await page.evaluate(async (theme) => {
          document.documentElement.dataset.theme = theme;
          await document.fonts.ready;
        }, theme);
        await page.addStyleTag({
          content:
            '*, *::before, *::after { animation: none !important; transition: none !important; }',
        });
        const phone = viewport.width < 860;
        const nav = page.locator(phone ? '.tabbar' : '.rail');
        // The rail's fixed destinations, excluding the dynamic app shortcuts.
        assert.equal(
          await (
            phone ? nav.locator('a, button') : nav.locator('.rail-group a, .rail-foot button')
          ).count(),
          phone ? 5 : 7,
        );
        if (label === 'Settings') {
          await page.getByRole('dialog', { name: 'Settings', exact: true }).waitFor();
        } else if (!phone || !['System', 'Automations'].includes(label)) {
          assert.equal(
            await nav
              .getByRole('link', { name: label, exact: label !== 'Library' })
              .getAttribute('aria-current'),
            'page',
          );
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
          path: path.join(shots, `${label.toLowerCase()}-${theme}-${viewport.width}.png`),
        });
      }
    }
  }
  // Real page interactions using the shared header, fields, and empty state.
  await page.goto(base + '/library');
  await page.getByRole('searchbox', { name: 'Search library' }).fill('no-such-fixture');
  await page.getByRole('heading', { name: 'No matches', exact: true }).waitFor();
  await page.goto(base + '/settings#notifications');
  await page.getByLabel('Topic', { exact: true }).fill('local-fixture-topic');
  assert.equal(await page.getByLabel('Topic', { exact: true }).inputValue(), 'local-fixture-topic');
  assert.deepEqual(errors, []);
  console.log(
    'PASS: seven dashboard routes, navigation, light/dark themes, desktop/phone layouts, library filtering and field labels' +
      (originalCss ? '; computed styles match the original CSS' : ''),
  );
} finally {
  await browser?.close();
  server.kill();
  if (server.exitCode === null) await new Promise((resolve) => server.once('exit', resolve));
}
