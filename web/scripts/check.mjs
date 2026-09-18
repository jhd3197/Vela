import { spawnSync } from 'node:child_process';
import { existsSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../..', import.meta.url));
const web = path.join(root, 'web');
const venv = path.join(
  root,
  process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python',
);
const python = process.env.VELA_TEST_PYTHON || (existsSync(venv) ? venv : 'python');
const npm = process.env.npm_execpath;
if (!npm) throw new Error('Run this check with npm --prefix web run check.');

function run(command, args, cwd = root) {
  const result = spawnSync(command, args, { cwd, stdio: 'inherit', windowsHide: true });
  if (result.error) console.error(result.error.message);
  if (result.status !== 0) process.exit(result.status || 1);
}

for (const script of ['lint', 'format:check']) run(process.execPath, [npm, 'run', script], web);
// Structural guards over the source tree. They are cheap and they fail with a
// file and a line, so they run before the test suites rather than after.
run(process.execPath, [path.join(web, 'scripts/ratchet.mjs')], web);
const tests = readdirSync(path.join(root, 'tests')).filter((name) => name.endsWith('.test.mjs'));
run(process.execPath, ['--test', ...tests.map((name) => path.join(root, 'tests', name))]);
run(python, ['-m', 'unittest', 'discover', '-s', 'tests']);
run(process.execPath, [npm, 'run', 'build'], web);
