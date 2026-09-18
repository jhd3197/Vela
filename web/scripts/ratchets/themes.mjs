// Theme ratchet: a bundled theme is readable, or it does not ship.
//
// Two scans under one id.
//
//   1. Every theme in `vela/assets/themes/` is what `build-themes.mjs` writes
//      from its recipe. A theme edited by hand is a theme nobody can regenerate
//      and, worse, one whose colours were chosen by eye against a promise the
//      dashboard makes in writing.
//   2. Every one of them clears the contrast gate in every base it declares:
//      text at 4.5:1 on each surface it can sit on, chrome and lines at 3:1,
//      the accent's own text on the accent's own tint at 4.5:1, and a card's
//      edge visible against the card. The high-contrast theme is held to 7:1
//      for body text, because it says it is built for reading.
//
// This is the difference between "the themes looked fine when they were made"
// and "no theme Vela ships can stop being readable without the check failing".
// An *imported* theme is warned about rather than refused: it is the user's own
// choice on their own computer, and this guard has no opinion about it.
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { bundled, gate, THEME_DIR } from '../build-themes.mjs';
import { rel } from './_lib.mjs';

export default {
  id: 'themes',
  describe:
    'A bundled theme that has drifted from its recipe, or that does not clear ' +
    'the contrast gate in a base it declares',
  fix: 'Run `node web/scripts/build-themes.mjs`, which refuses to write a theme that fails.',
  scan() {
    const findings = [];
    const expected = new Map(bundled().map((theme) => [`${theme.slug}.json`, theme]));

    let present;
    try {
      present = readdirSync(THEME_DIR).filter((name) => name.endsWith('.json'));
    } catch {
      // No directory at all is one finding, not one per theme.
      return [{ file: rel(THEME_DIR), line: 1, key: 'no bundled themes are installed' }];
    }

    for (const name of present) {
      const file = rel(path.join(THEME_DIR, name));
      const theme = expected.get(name);
      if (!theme) {
        findings.push({ file, line: 1, key: 'this theme has no recipe in build-themes.mjs' });
        continue;
      }
      const committed = readFileSync(path.join(THEME_DIR, name), 'utf8').replace(/\r\n/g, '\n');
      if (committed !== `${JSON.stringify(theme, null, 2)}\n`) {
        findings.push({ file, line: 1, key: 'has drifted from what its recipe produces' });
      }
    }
    for (const name of expected.keys()) {
      if (!present.includes(name)) {
        findings.push({ file: rel(path.join(THEME_DIR, name)), line: 1, key: 'is missing' });
      }
    }

    for (const failure of gate()) {
      findings.push({
        file: rel(path.join(THEME_DIR, `${failure.slug}.json`)),
        line: 1,
        key:
          `${failure.base}: ${failure.ink} on ${failure.surface} is ` +
          `${failure.ratio}:1, and needs ${failure.min}:1`,
      });
    }

    return findings.sort((a, b) => a.file.localeCompare(b.file) || a.key.localeCompare(b.key));
  },
};
