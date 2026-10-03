// Orphan-partial ratchet.
//
// A partial nothing imports is a stylesheet that does not exist. Settings ›
// General shipped browser-default name fields because their rules lived in
// `pages/_settings.scss`, which no `@use` ever reached: the file looked like
// the owner of those classes, a reader trusted it, and the build never said a
// word. This walks the `@use` graph from each entry stylesheet and flags every
// partial under `web/src/styles` the walk did not reach.
import { existsSync } from 'node:fs';
import path from 'node:path';
import { readLines, repoRoot, sourceFiles } from './_lib.mjs';

// The stylesheets a module imports directly; everything else is a partial.
const ENTRIES = ['web/src/styles/main.scss', 'web/src/agent-host/agent-host.scss'];
const USE = /^\s*@(?:use|forward|import)\s+['"]([^'"]+)['"]/;

function resolve(from, target) {
  if (/^(https?:|sass:)/.test(target)) return null;
  const dir = path.posix.dirname(from);
  const joined = path.posix.normalize(path.posix.join(dir, target));
  const base = path.posix.basename(joined);
  const folder = path.posix.dirname(joined);
  const candidates = [
    `${folder}/_${base}.scss`,
    `${folder}/${base}.scss`,
    `${joined}/_index.scss`,
    joined,
  ];
  return candidates.find((file) => existsSync(path.join(repoRoot, file))) || null;
}

export default {
  id: 'orphan-partials',
  describe: 'An SCSS partial under web/src/styles that no entry stylesheet reaches by @use.',
  fix: 'Add an @use for it to main.scss (in cascade order), or delete it if nothing needs it.',
  scan() {
    const reached = new Set();
    const queue = ENTRIES.filter((file) => existsSync(path.join(repoRoot, file)));
    while (queue.length) {
      const file = queue.pop();
      if (reached.has(file)) continue;
      reached.add(file);
      for (const text of readLines(file)) {
        const match = USE.exec(text);
        const next = match && resolve(file, match[1]);
        if (next && !reached.has(next)) queue.push(next);
      }
    }
    return sourceFiles(['.scss'])
      .filter((file) => file.startsWith('web/src/styles/') && !reached.has(file))
      .map((file) => ({ file, line: 1, key: 'never imported' }));
  },
};
