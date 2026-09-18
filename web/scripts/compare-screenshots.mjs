/**
 * Capture the dashboard's surfaces, and compare two captures pixel by pixel.
 *
 *   node web/scripts/compare-screenshots.mjs capture <directory> [port]
 *   node web/scripts/compare-screenshots.mjs compare <before> <after> [diff-directory]
 *
 * This is what a change to who owns a CSS class is checked against: capture
 * before, make the change, capture after, compare. A cascade that shifted
 * shows up as a percentage rather than as a feeling.
 *
 * `capture` starts its own disposable engine on a temporary data directory
 * (`scripts/serve-release-fixtures.py`) and reads the built dashboard from
 * `web/dist`. It never touches an installed server or a user's own data, which
 * is why it does not reuse `capture-screenshots.mjs` — that one deliberately
 * attaches to a hub on port 7700.
 *
 * `compare` decodes both PNGs in the browser that took them, so it needs no
 * image library and adds no dependency. Given a third argument it also writes
 * one diff image per screen there, painting every changed pixel and dimming
 * the rest: a percentage says how much moved, and only the picture says what.
 * A phase is never closed on the number alone.
 */
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

// The surfaces whose stylesheets the ownership migration touches: the rail and
// the workspace around every page, the desk, a drawer, a dialog, the Ask page
// and the app view, at a desktop width and a phone width, in both themes.
const SURFACES = [
  ['desk', '/'],
  ['library', '/library'],
  ['ask', '/ask'],
  ['automations', '/automations'],
  ['files', '/files'],
  ['settings', '/settings'],
];
const SIZES = [
  ['wide', 1366, 900],
  ['phone', 390, 844],
];
const THEMES = ['light', 'dark'];

async function waitForEngine(base, child, output) {
  for (let i = 0; i < 200; i++) {
    if (child.exitCode !== null) throw new Error(output());
    try {
      if ((await fetch(`${base}/api/health`)).ok) return;
    } catch {
      // Not listening yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`the disposable engine did not start: ${output()}`);
}

async function capture(directory, port) {
  let text = '';
  const engine = spawn(python, ['scripts/serve-release-fixtures.py', String(port)], {
    cwd: root,
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  engine.stderr.on('data', (data) => {
    text += data;
  });
  const base = `http://127.0.0.1:${port}`;
  let browser;
  try {
    await waitForEngine(base, engine, () => text);
    await fs.mkdir(directory, { recursive: true });
    browser = await chromium.launch({
      headless: true,
      channel: process.env.VELA_BROWSER_CHANNEL || 'chrome',
    });
    const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
    await page.addInitScript(() => {
      try {
        localStorage.setItem('vela.welcome.v1', 'done');
        localStorage.setItem('vela-developer-tools', 'on');
      } catch {
        // Blocked site data: the capture still renders.
      }
    });
    for (const [label, width, height] of SIZES) {
      await page.setViewportSize({ width, height });
      for (const theme of THEMES) {
        for (const [name, route] of SURFACES) {
          await page.goto(`${base}${route}`, { waitUntil: 'load' });
          await page.locator('.rail').waitFor();
          // Animations and the wallpaper fade would each make two captures of
          // the same page differ for reasons that are not the stylesheet.
          await page.addStyleTag({
            content:
              '*, *::before, *::after { animation: none !important; transition: none !important; }',
          });
          // The base is set *after* the dashboard has settled, not before:
          // `ThemeSync` writes `data-theme` from the server's own setting once
          // settings load, so a base chosen before that is overwritten and both
          // captures come out in whichever base the fixture server prefers.
          await page.waitForTimeout(600);
          await page.evaluate((value) => {
            document.documentElement.dataset.theme = value;
          }, theme);
          await page.waitForTimeout(150);
          await page.screenshot({
            path: path.join(directory, `${name}-${label}-${theme}.png`),
            fullPage: false,
          });
        }
      }
    }
    console.log(
      `captured ${SURFACES.length * SIZES.length * THEMES.length} shots into ${directory}`,
    );
  } finally {
    await browser?.close();
    engine.kill();
  }
}

async function compare(before, after, diffDirectory) {
  const names = (await fs.readdir(before)).filter((name) => name.endsWith('.png')).sort();
  assert.ok(names.length, `no shots in ${before}`);
  const browser = await chromium.launch({
    headless: true,
    channel: process.env.VELA_BROWSER_CHANNEL || 'chrome',
  });
  let worst = 0;
  if (diffDirectory) await fs.mkdir(diffDirectory, { recursive: true });
  try {
    const page = await browser.newPage();
    for (const name of names) {
      const load = async (directory) =>
        `data:image/png;base64,${(await fs.readFile(path.join(directory, name))).toString('base64')}`;
      const measured = await page.evaluate(
        async ([left, right, wantDiff]) => {
          const decode = (source) =>
            new Promise((resolve, reject) => {
              const image = new Image();
              image.onload = () => resolve(image);
              image.onerror = reject;
              image.src = source;
            });
          const [a, b] = await Promise.all([decode(left), decode(right)]);
          if (a.width !== b.width || a.height !== b.height) return 100;
          const pixels = (image) => {
            const canvas = document.createElement('canvas');
            canvas.width = image.width;
            canvas.height = image.height;
            const context = canvas.getContext('2d', { willReadFrequently: true });
            context.drawImage(image, 0, 0);
            return context.getImageData(0, 0, image.width, image.height).data;
          };
          const [one, two] = [pixels(a), pixels(b)];
          const canvas = document.createElement('canvas');
          canvas.width = a.width;
          canvas.height = a.height;
          const context = canvas.getContext('2d');
          const out = context.createImageData(a.width, a.height);
          let differing = 0;
          for (let i = 0; i < one.length; i += 4) {
            // A channel that moved by one is the encoder, not the cascade.
            const moved =
              Math.abs(one[i] - two[i]) > 1 ||
              Math.abs(one[i + 1] - two[i + 1]) > 1 ||
              Math.abs(one[i + 2] - two[i + 2]) > 1 ||
              Math.abs(one[i + 3] - two[i + 3]) > 1;
            if (moved) differing += 1;
            if (!wantDiff) continue;
            if (moved) {
              // Magenta on what moved: no interface in this system is magenta,
              // so a changed pixel cannot be mistaken for the screen itself.
              out.data[i] = 255;
              out.data[i + 1] = 0;
              out.data[i + 2] = 190;
            } else {
              // The rest stays as a faint ghost of the "after" shot, so a
              // change can be found on the screen it happened on.
              const grey = (two[i] * 0.2126 + two[i + 1] * 0.7152 + two[i + 2] * 0.0722) * 0.35 + 150;
              out.data[i] = grey;
              out.data[i + 1] = grey;
              out.data[i + 2] = grey;
            }
            out.data[i + 3] = 255;
          }
          const percent = (differing / (one.length / 4)) * 100;
          if (!wantDiff) return { percent, diff: null };
          context.putImageData(out, 0, 0);
          return { percent, diff: canvas.toDataURL('image/png') };
        },
        [await load(before), await load(after), Boolean(diffDirectory)],
      );
      const percent = measured.percent;
      if (measured.diff) {
        await fs.writeFile(
          path.join(diffDirectory, name),
          Buffer.from(measured.diff.split(',')[1], 'base64'),
        );
      }
      worst = Math.max(worst, percent);
      console.log(`${percent.toFixed(3).padStart(8)} %  ${name}`);
    }
  } finally {
    await browser.close();
  }
  console.log(`\nworst: ${worst.toFixed(3)} %`);
  return worst;
}

const [command, first, second, third] = process.argv.slice(2);
if (command === 'capture') {
  assert.ok(first, 'usage: compare-screenshots.mjs capture <directory> [port]');
  await capture(path.resolve(first), Number(second) || 17733);
} else if (command === 'compare') {
  assert.ok(first && second, 'usage: compare-screenshots.mjs compare <before> <after> [diff]');
  await compare(path.resolve(first), path.resolve(second), third ? path.resolve(third) : null);
} else {
  console.error('usage: compare-screenshots.mjs capture <directory> [port]');
  console.error('       compare-screenshots.mjs compare <before> <after> [diff-directory]');
  process.exit(1);
}
