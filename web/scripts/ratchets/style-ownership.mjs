// Style-ownership ratchet.
//
// Origin: ServerKit `frontend/scripts/check-style-ownership.mjs` (MIT, same
// owner). A class defined at the top level of more than one partial has no
// owner: every definition applies, merged per property in import order, so the
// rendered element is a composite nobody wrote and editing one file changes
// only the properties that file happens to win.
//
// Column zero only. A nested `.foo { }` is scoped by its parent and is not
// competing for ownership of the bare class. Definitions are counted, not
// names: counting names would let a class that is already shared spread to a
// fourth and a fifth partial without moving the number, which is the failure
// this is meant to stop.
import { readLines, sourceFiles } from './_lib.mjs';

const TOP_LEVEL_CLASS = /^\.([A-Za-z][\w-]*)\s*[,{]/;

export default {
  id: 'style-ownership',
  describe: 'A class defined at the top level of more than one SCSS partial.',
  fix: 'Give the class one owning partial and scope page variants under the page root class.',
  scan() {
    const definitions = new Map();
    for (const file of sourceFiles(['.scss'])) {
      let depth = 0;
      readLines(file).forEach((text, index) => {
        const match = depth === 0 ? TOP_LEVEL_CLASS.exec(text) : null;
        if (match) {
          if (!definitions.has(match[1])) definitions.set(match[1], []);
          definitions.get(match[1]).push({ file, line: index + 1 });
        }
        for (const character of text) {
          if (character === '{') depth += 1;
          else if (character === '}') depth -= 1;
        }
      });
    }

    const findings = [];
    for (const [name, sites] of definitions) {
      const owners = new Set(sites.map((site) => site.file));
      if (owners.size < 2) continue;
      for (const site of sites) {
        findings.push({
          ...site,
          key: `.${name} is also defined in ${owners.size - 1} other partial(s)`,
        });
      }
    }
    return findings.sort((a, b) => a.file.localeCompare(b.file) || a.line - b.line);
  },
};
