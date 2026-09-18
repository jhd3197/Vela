// Shared helpers for the ratchet guards. Node built-ins only: a guard must be
// readable and runnable without anything installed.
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const repoRoot = fileURLToPath(new URL('../../..', import.meta.url));

/** Repository-relative, forward-slashed: the key a baseline entry is stored under. */
export function rel(absolute) {
  return path.relative(repoRoot, absolute).split(path.sep).join('/');
}

export function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? walk(full) : [full];
  });
}

/** Every file under `web/src` with one of the given extensions, repo-relative. */
export function sourceFiles(extensions) {
  const wanted = new Set(extensions);
  return walk(path.join(repoRoot, 'web', 'src'))
    .filter((file) => wanted.has(path.extname(file)))
    .map(rel)
    .sort();
}

export function readLines(file) {
  return readFileSync(path.join(repoRoot, file), 'utf8').split(/\r?\n/);
}

/**
 * One finding per match of `pattern` in `file`. The runner, not the guard,
 * decides whether a line carries an escape comment.
 */
export function matchLines(file, pattern, describe = (match) => match[0]) {
  const findings = [];
  readLines(file).forEach((text, index) => {
    for (const match of text.matchAll(pattern)) {
      findings.push({ file, line: index + 1, key: describe(match, text) });
    }
  });
  return findings;
}
