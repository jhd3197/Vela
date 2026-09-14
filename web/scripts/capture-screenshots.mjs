/**
 * Capture README screenshots from a real running Vela hub.
 *
 *   npm run shots
 *
 * What it does:
 *   1. Builds the frontend if web/dist is missing.
 *   2. Starts `python -m vela` against a throwaway VELA_DATA_DIR (your real
 *      ~/.vela is never touched), installs a few built-in apps so the hub
 *      looks lived-in, and launches one so it shows as Running.
 *      If a hub is already serving on 127.0.0.1:7700 it is reused as-is.
 *   3. Drives Chromium (Playwright) through the hub pages in dark theme at
 *      desktop and iPhone sizes, saving PNGs to docs/screenshots/.
 *
 * docs/screenshots/ is gitignored — the shots are generated artifacts.
 */

import { chromium, devices } from 'playwright';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const webRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const repoRoot = path.dirname(webRoot);
const outDir = path.join(repoRoot, 'docs', 'screenshots');
const baseUrl = 'http://127.0.0.1:7700';

const DEMO_APPS = ['health', 'meals', 'notes'];
const RUNNING_APP = 'health';

const DESKTOP_SHOTS = [
  ['/', 'home.png'],
  ['/apps', 'apps.png'],
  ['/library', 'library.png'],
  [`/app/${RUNNING_APP}`, 'app-view.png'],
  ['/settings', 'settings.png'],
];

const MOBILE_SHOTS = [
  ['/', 'mobile-home.png'],
  ['/apps', 'mobile-apps.png'],
];

// The hub ships its own Add-to-Home-Screen panel (components/AddToHomeScreen.jsx):
// iOS gets the Share -> Add to Home Screen steps in the app detail drawer, and
// Settings carries the hub-wide install section. Capture both for the README's
// "Install Vela as an App" section.
async function shootInstallPanels(desktopPage, mobilePage) {
  await desktopPage.goto(`${baseUrl}/settings`, { waitUntil: 'networkidle' });
  const desktopPanel = desktopPage.locator('.a2hs');
  await desktopPanel.scrollIntoViewIfNeeded();
  await desktopPage.waitForTimeout(400);
  await desktopPanel.screenshot({ path: path.join(outDir, 'install-desktop.png') });
  console.log('✓ install-desktop.png  (/settings .a2hs)');

  await mobilePage.goto(`${baseUrl}/apps`, { waitUntil: 'networkidle' });
  await mobilePage.getByText('Health', { exact: true }).first().click();
  const iosPanel = mobilePage.locator('.a2hs');
  await iosPanel.waitFor();
  await iosPanel.scrollIntoViewIfNeeded();
  await mobilePage.waitForTimeout(400);
  await iosPanel.screenshot({ path: path.join(outDir, 'install-ios.png') });
  console.log('✓ install-ios.png  (/apps -> Health drawer .a2hs)');
}

async function hubHealthy() {
  try {
    const res = await fetch(`${baseUrl}/api/health`);
    return res.ok;
  } catch {
    return false;
  }
}

async function waitForHub(timeoutMs = 30_000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await hubHealthy()) return true;
    await new Promise((r) => setTimeout(r, 400));
  }
  return false;
}

function ensureBuild() {
  if (fs.existsSync(path.join(webRoot, 'dist', 'index.html'))) return Promise.resolve();
  console.log('web/dist missing — running npm run build…');
  const build = spawn('npm', ['run', 'build'], {
    cwd: webRoot,
    stdio: 'inherit',
    shell: true,
  });
  return new Promise((resolve, reject) => {
    build.on('exit', (code) => (code === 0 ? resolve() : reject(new Error('build failed'))));
  });
}

async function installDemoApps() {
  const session = await fetch(`${baseUrl}/api/session`, { headers: { 'X-Vela-Bootstrap': '1' } });
  if (!session.ok) throw new Error('Local hub authentication failed');
  const headers = { Authorization: `Bearer ${(await session.json()).token}` };
  for (const id of DEMO_APPS) {
    let res = await fetch(`${baseUrl}/api/apps/${id}/install`, { method: 'POST', headers });
    if (res.status === 409) {
      const prepared = await fetch(`${baseUrl}/api/releases/prepare`, {
        method: 'POST',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ app_id: id }),
      });
      if (!prepared.ok)
        throw new Error(`Fixture release preparation failed: ${await prepared.text()}`);
      const review = await prepared.json();
      res = await fetch(`${baseUrl}/api/releases/${review.review}/commit`, {
        method: 'POST',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ capabilities: review.capabilities, operations: review.operations }),
      });
    }
    if (!res.ok) throw new Error(`Fixture install failed: ${await res.text()}`);
    console.log(`install ${id}: ${res.status}`);
  }
  const res = await fetch(`${baseUrl}/api/apps/${RUNNING_APP}/launch`, { method: 'POST', headers });
  console.log(`launch ${RUNNING_APP}: ${res.status}`);
  // Give the process a moment to bind its port.
  await new Promise((r) => setTimeout(r, 1500));
}

async function shoot(page, route, filename) {
  await page.goto(baseUrl + route, { waitUntil: 'networkidle' });
  await page.waitForTimeout(800);
  await page.screenshot({ path: path.join(outDir, filename) });
  console.log(`✓ ${filename}  (${route})`);
}

async function main() {
  fs.mkdirSync(outDir, { recursive: true });
  await ensureBuild();

  let backend = null;
  let dataDir = null;
  if (await hubHealthy()) {
    console.log('hub already running on :7700 — capturing against it as-is');
  } else {
    dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vela-shots-'));
    console.log(`starting hub with throwaway data dir ${dataDir}`);
    backend = spawn('python', ['scripts/serve-screenshot-fixtures.py'], {
      cwd: repoRoot,
      env: { ...process.env, VELA_DATA_DIR: dataDir },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    backend.stderr.on('data', (chunk) => process.stderr.write(chunk));
    if (!(await waitForHub())) {
      console.error('hub did not come up in time');
      backend.kill();
      process.exit(1);
    }
    await installDemoApps();
  }

  const browser = await chromium.launch();

  try {
    const desktop = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      deviceScaleFactor: 2,
      colorScheme: 'dark',
    });
    await desktop.addInitScript(() => localStorage.setItem('vela-theme', 'dark'));
    const page = await desktop.newPage();
    for (const [route, file] of DESKTOP_SHOTS) await shoot(page, route, file);

    const mobile = await browser.newContext({
      ...devices['iPhone 13'],
      colorScheme: 'dark',
    });
    await mobile.addInitScript(() => localStorage.setItem('vela-theme', 'dark'));
    const mPage = await mobile.newPage();
    for (const [route, file] of MOBILE_SHOTS) await shoot(mPage, route, file);

    await shootInstallPanels(page, mPage);
    await desktop.close();
    await mobile.close();
  } finally {
    await browser.close();
    if (backend) backend.kill();
    if (dataDir) fs.rmSync(dataDir, { recursive: true, force: true });
  }

  console.log(`\nDone — screenshots in ${path.relative(repoRoot, outDir)}/`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
