// The browser foundations fixture, sequenced under the runner.
//
// `assert-foundations.mjs` is the oldest structural guard the dashboard has:
// one engine request across consumers and routes, shared retries and cleanup,
// and nested modal focus, keyboard and pending behaviour at two widths. It is
// a Playwright fixture, and `docs/TESTING.md` keeps browser acceptance a
// separate local step — `npm --prefix web run check` must stay runnable on a
// machine with no browser installed. So the runner owns its registration and
// checks statically that the fixture is still wired up; `test-shared-ui.mjs`
// is what executes it.
//
// A finding here means the fixture has been orphaned: still on disk, no longer
// run by anything, and therefore no longer guarding anything.
import { existsSync } from 'node:fs';
import path from 'node:path';
import { readLines, repoRoot } from './_lib.mjs';

const RUNNER = 'web/scripts/test-shared-ui.mjs';
const REQUIRED = [
  'web/scripts/assert-foundations.mjs',
  'web/scripts/fixtures/foundations.jsx',
  'web/scripts/fixtures/shared-ui.html',
  'web/scripts/fixtures/shared-ui.jsx',
];

export default {
  id: 'foundations',
  describe: 'The browser foundations fixture stays sequenced by the shared-UI suite.',
  fix: `Keep ${RUNNER} importing and calling checkFoundations(), or delete the fixture deliberately.`,
  scan() {
    const findings = [];
    for (const file of REQUIRED) {
      if (!existsSync(path.join(repoRoot, file))) {
        findings.push({ file: RUNNER, line: 1, key: `${file} is missing` });
      }
    }
    if (findings.length) return findings;

    const lines = readLines(RUNNER);
    const imported = lines.findIndex((text) =>
      /import\s*\{[^}]*\bcheckFoundations\b[^}]*\}\s*from\s*'\.\/assert-foundations\.mjs'/.test(
        text,
      ),
    );
    const called = lines.findIndex((text) => /\bcheckFoundations\s*\(/.test(text));
    if (imported === -1) {
      findings.push({ file: RUNNER, line: 1, key: 'checkFoundations is no longer imported' });
    }
    if (called === -1 || called === imported) {
      findings.push({ file: RUNNER, line: 1, key: 'checkFoundations is no longer called' });
    }
    return findings;
  },
};
