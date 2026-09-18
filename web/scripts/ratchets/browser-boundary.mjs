// Browser-boundary ratchet.
//
// Origin: ServerKit `frontend/scripts/check-frontend-boundaries.mjs` (MIT,
// same owner). Browser storage, the clipboard and opening a second window all
// fail in ways a page has to decide about once: storage throws in private mode
// and with site data blocked, `navigator.clipboard` does not exist over plain
// HTTP, and a window opened without `noopener` hands the new tab a handle on
// this one. Fourteen files had each answered those questions separately, and
// not identically. `web/src/storage.js` and `web/src/clipboard.js` are the
// answers; this guard is what keeps a fifteenth from appearing.
import { matchLines, sourceFiles } from './_lib.mjs';

const DOORS = new Set(['web/src/storage.js', 'web/src/clipboard.js']);

const PATTERNS = [
  [/(?:\bwindow\s*\.\s*)?\b(?:localStorage|sessionStorage)\s*\./g, 'use web/src/storage.js'],
  [/\bnavigator\s*\.\s*clipboard\b/g, 'use copyText() from web/src/clipboard.js'],
  [/(?:\bwindow\s*\.\s*)?\bopen\s*\(\s*[`'"/]/g, 'use openExternal() from web/src/clipboard.js'],
];

export default {
  id: 'browser-boundary',
  describe: 'Browser storage, clipboard and window.open outside the shared helpers.',
  fix: 'Route the call through web/src/storage.js or web/src/clipboard.js.',
  scan() {
    const findings = [];
    for (const file of sourceFiles(['.js', '.jsx'])) {
      if (DOORS.has(file)) continue;
      for (const [pattern, advice] of PATTERNS) {
        findings.push(...matchLines(file, pattern, (match) => `${match[0].trim()} — ${advice}`));
      }
    }
    return findings.sort((a, b) => a.file.localeCompare(b.file) || a.line - b.line);
  },
};
