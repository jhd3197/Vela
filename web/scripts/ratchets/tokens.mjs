// Token ratchet: colour and rhythm are written in one place.
//
// Three scans under one id, because they are one rule seen from three sides —
// the token sheet is the only file that may name a colour.
//
//   1. `_tokens.scss` is generated. Regenerating it and comparing catches a
//      hand edit the moment it is made, rather than three commits later when
//      someone runs the generator and wonders why the diff is large.
//   2. Every other SCSS partial is scanned for a hex, `rgb()` or `hsl()`.
//      A colour there is a value the theme cannot reach, so an imported theme
//      leaves it behind and the surface it paints stops matching the rest.
//   3. A partial that has declared itself migrated with a `// tokens: spacing`
//      header may not write a raw pixel padding, gap or margin of 3px or more.
//
// The plan this implements asked for an exemption list inside the script, each
// entry with a reason. The ratchet baseline is that list and a better one: an
// exemption is a number that has to come down rather than a name that sits
// there, `--report` prints every one, and a file that is already exempt cannot
// quietly grow a second literal. A genuinely permanent one — the per-app icon
// gradients, which are art generated from an app id and not interface colour —
// takes a `// ratchet: allow tokens <reason>` line escape, which is counted.
import { readLines, repoRoot, sourceFiles } from './_lib.mjs';
import { sheet } from '../build-tokens.mjs';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const SHEET = 'web/src/styles/_tokens.scss';
// `rgba(var(--scrim-rgb), 0.5)` is a token read at an alpha, not a literal. The
// three channel tokens exist so a veil over a photograph keeps the compositing
// it always had, and flagging them would push the stylesheet back to writing
// the numbers out by hand, which is the thing this guard is for.
const COLOUR = /#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\((?!\s*var\(--)/g;
// `padding: 12px`, `gap: 10px 4px`, `margin-top: 22px` — the three properties
// that carry rhythm. A width or a height is a dimension and is left alone.
const RHYTHM = /^\s*(padding|gap|margin)(-(top|right|bottom|left|inline|block)\w*)?\s*:([^;]*);/;
// A negative value is a nudge that pulls something back -- half an element's
// own height, usually -- rather than rhythm between two things, so it is not
// held to the scale.
const PIXELS = /(?<![\d.-])(\d+(?:\.\d+)?)px/g;
const SPACING_MARK = '// tokens: spacing';
// The ends of `--space-1` … `--space-8`, which is the range a value can be on.
const SCALE_FLOOR = 3;
const SCALE_CEILING = 28;

export default {
  id: 'tokens',
  describe:
    'A colour written outside the generated token sheet, a hand edit to that ' +
    'sheet, or a raw pixel padding/gap/margin in a partial marked ' +
    `"${SPACING_MARK}"`,
  fix:
    'Read the colour from a token or a ramp step, run `node web/scripts/build-tokens.mjs` ' +
    'after changing web/src/design/, and use --space-1…8 for rhythm.',
  scan() {
    const findings = [];

    const committed = readFileSync(path.join(repoRoot, SHEET), 'utf8').replace(/\r\n/g, '\n');
    if (committed !== sheet()) {
      findings.push({
        file: SHEET,
        line: 1,
        key: 'the generated token sheet has drifted from web/src/design/theme.vela.json',
      });
    }

    for (const file of sourceFiles(['.scss'])) {
      if (file === SHEET) continue;
      const lines = readLines(file);
      const migrated = lines.some((text) => text.includes(SPACING_MARK));
      lines.forEach((text, index) => {
        const line = index + 1;
        for (const match of text.matchAll(COLOUR)) {
          findings.push({ file, line, key: `${match[0].replace('(', '(…)')} is written here` });
        }
        if (!migrated) return;
        const rhythm = RHYTHM.exec(text);
        if (!rhythm) return;
        for (const pixels of rhythm[4].matchAll(PIXELS)) {
          // The scale runs 3px to 28px. Below it a hairline or a two-pixel
          // nudge is not rhythm and rounding one up is how a 1px rule becomes a
          // visible band. Above it there is no step to reach for: an 80px
          // offset under an empty state is a dimension the layout chose, and
          // crushing it to 28px would be a layout change wearing a token.
          const value = Number(pixels[1]);
          if (value >= SCALE_FLOOR && value <= SCALE_CEILING) {
            findings.push({ file, line, key: `${rhythm[1]}: ${pixels[0]} is not on the scale` });
          }
        }
      });
    }

    return findings.sort((a, b) => a.file.localeCompare(b.file) || a.line - b.line);
  },
};
