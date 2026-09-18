// Status-vocabulary ratchet.
//
// Origin: ServerKit `frontend/scripts/check-status-one-door.mjs` (MIT, same
// owner). When every surface keeps its own status table, the same run reads
// "Waiting for you" on one page and "waiting" on another, and a status added
// to the engine reaches some of them and not the rest. `web/src/operations/
// status.js` is the one door: it owns the label and the tone for every status
// a background operation can be in.
//
// The smell this looks for is an object literal that maps three or more
// status words to strings. Three, because two is a pair of flags; a third
// makes it a vocabulary. A map that is genuinely not about a status — an
// ordering, an icon set — can carry `// ratchet: allow status-vocabulary
// <reason>` on the line the literal opens.
import { readLines, sourceFiles } from './_lib.mjs';

const DOOR = 'web/src/operations/status.js';
const STATUS_WORDS = [
  'running',
  'failed',
  'pending',
  'queued',
  'done',
  'waiting',
  'error',
  'ok',
  'warn',
];
// A key, quoted or bare, whose value is a string literal.
const STRING_ENTRY = /(?:^|[{,\s])['"]?([A-Za-z_][\w-]*)['"]?\s*:\s*['"`]/g;
// Innermost literals only: a flat status table never nests, and not matching
// across a nested brace is what keeps a whole module from reading as one map.
const FLAT_LITERAL = /\{[^{}]*\}/gs;

export default {
  id: 'status-vocabulary',
  describe: `An object literal mapping three or more status words to strings outside ${DOOR}.`,
  fix: `Read the label and the tone from ${DOOR} instead of keeping a local table.`,
  scan() {
    const words = new Set(STATUS_WORDS);
    const findings = [];
    for (const file of sourceFiles(['.js', '.jsx'])) {
      if (file === DOOR) continue;
      const lines = readLines(file);
      const source = lines.join('\n');
      for (const literal of source.matchAll(FLAT_LITERAL)) {
        const matched = new Set();
        for (const entry of literal[0].matchAll(STRING_ENTRY)) {
          if (words.has(entry[1])) matched.add(entry[1]);
        }
        if (matched.size < 3) continue;
        const line = source.slice(0, literal.index).split('\n').length;
        findings.push({
          file,
          line,
          key: `status map over ${[...matched].sort().join(', ')}`,
        });
      }
    }
    return findings.sort((a, b) => a.file.localeCompare(b.file) || a.line - b.line);
  },
};
