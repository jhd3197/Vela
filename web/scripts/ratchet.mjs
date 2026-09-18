#!/usr/bin/env node
// The dashboard's ratchet runner.
//
// Origin: ServerKit `frontend/scripts/check-*.mjs` and its
// `lint-warning-baseline.json` (MIT, same owner). ServerKit keeps one script
// and one ceiling file per guard; here one runner owns the comparison, so a
// guard is only its scan and every guard can land the day it is written with
// today's numbers instead of waiting for a clean sweep.
//
// A guard is a module in `scripts/ratchets/` whose default export is
// `{ id, describe, fix, scan() -> [{ file, line, key }] }`. A file whose name
// starts with `_` is a helper, not a guard.
//
// `ratchets/baseline.json` is keyed by guard id, then by repository-relative
// file, and holds how many findings that file is allowed. A file that is
// absent has a limit of zero, so the first violation in a clean file fails.
// A count that grew fails. A count that fell fails too, asking for `--update`:
// a baseline left above its floor is a ratchet that has stopped ratcheting.
//
// Usage (from web/):
//   node scripts/ratchet.mjs                   # check
//   node scripts/ratchet.mjs --report          # list every finding
//   node scripts/ratchet.mjs --update          # rewrite the baseline
//   node scripts/ratchet.mjs --only <id>       # one guard
//   node scripts/ratchet.mjs --update --allow-growth <id>
//
// A single line may opt out with `// ratchet: allow <id> <reason>`. The escape
// is counted and `--report` lists every one, so an allowlist cannot grow
// quietly.
import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { readLines, rel } from './ratchets/_lib.mjs';

const guardsUrl = new URL('./ratchets/', import.meta.url);
const guardsDir = fileURLToPath(guardsUrl);
const baselinePath = path.join(guardsDir, 'baseline.json');

const argv = process.argv.slice(2);
const flag = (name) => argv.includes(name);
const only = argv[argv.indexOf('--only') + 1] || null;
const allowGrowth = new Set(argv.filter((arg, index) => argv[index - 1] === '--allow-growth'));

const guards = [];
for (const name of readdirSync(guardsDir).sort()) {
  if (!name.endsWith('.mjs') || name.startsWith('_')) continue;
  const guard = (await import(new URL(name, guardsUrl))).default;
  if (!guard?.id || typeof guard.scan !== 'function') {
    throw new Error(`ratchets/${name} must default-export { id, describe, scan }.`);
  }
  if (!flag('--only') || guard.id === only) guards.push(guard);
}
if (flag('--only') && guards.length === 0) throw new Error(`No ratchet guard is called "${only}".`);

let baseline = {};
try {
  baseline = JSON.parse(readFileSync(baselinePath, 'utf8'));
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
}

// One read per file, shared by every guard that reports a line in it.
const lineCache = new Map();
const lineAt = (file, line) => {
  if (!lineCache.has(file)) lineCache.set(file, readLines(file));
  return lineCache.get(file)[line - 1] ?? '';
};
// `// ratchet: allow <id> <reason>` — the reason is not optional, which is why
// the match runs to the space after the id.
const escaped = (guardId, finding) =>
  lineAt(finding.file, finding.line).split(/\s+/).join(' ').includes(`ratchet: allow ${guardId} `);

const byName = ([a], [b]) => a.localeCompare(b);
const failures = [];
const shrinks = [];
const next = {};
let totalFindings = 0;
let totalEscapes = 0;

for (const guard of guards) {
  const counted = [];
  const allowed = [];
  for (const finding of guard.scan()) {
    (escaped(guard.id, finding) ? allowed : counted).push(finding);
  }

  const actual = new Map();
  for (const finding of counted) actual.set(finding.file, (actual.get(finding.file) || 0) + 1);
  const limits = baseline[guard.id] || {};
  totalFindings += counted.length;
  totalEscapes += allowed.length;
  next[guard.id] = Object.fromEntries([...actual].sort(byName));

  for (const [file, count] of [...actual].sort(byName)) {
    const limit = limits[file] || 0;
    if (count <= limit) continue;
    failures.push({
      guard: guard.id,
      text: [
        `  ${guard.id}: ${file} has ${count} finding(s); the baseline allows ${limit}.`,
        ...counted
          .filter((finding) => finding.file === file)
          .map((finding) => `      ${file}:${finding.line}  ${finding.key}`),
        `      ${guard.fix || guard.describe}`,
      ].join('\n'),
    });
  }
  for (const [file, limit] of Object.entries(limits)) {
    const count = actual.get(file) || 0;
    if (count < limit) shrinks.push(`  ${guard.id}: ${file} is down to ${count} from ${limit}.`);
  }

  if (flag('--report')) {
    console.log(`\n${guard.id} — ${guard.describe}`);
    if (counted.length === 0 && allowed.length === 0) console.log('  (no findings)');
    for (const finding of counted) console.log(`  ${finding.file}:${finding.line}  ${finding.key}`);
    for (const finding of allowed) {
      console.log(`  allow  ${finding.file}:${finding.line}  ${finding.key}`);
    }
  }
}

if (flag('--update')) {
  const grew = failures.filter((failure) => !allowGrowth.has(failure.guard));
  if (grew.length) {
    console.error('\nRatchet baselines cannot be raised silently:\n');
    grew.forEach((failure) => console.error(failure.text));
    console.error('\n  Fix the finding, or re-run with --allow-growth <guard id>.\n');
    process.exit(1);
  }
  // `--only` updates one guard without discarding what the others recorded.
  const merged = flag('--only') ? { ...baseline, ...next } : next;
  const ordered = Object.fromEntries(
    Object.keys(merged)
      .sort()
      .map((id) => [id, merged[id]]),
  );
  writeFileSync(baselinePath, `${JSON.stringify(ordered, null, 2)}\n`);
  console.log(`ratchet: baseline written for ${guards.length} guard(s).`);
  process.exit(0);
}

if (failures.length) {
  console.error('\nRatchet check failed:\n');
  failures.forEach((failure) => console.error(failure.text));
  console.error('');
  process.exit(1);
}

if (shrinks.length) {
  console.error('\nRatchet baselines can shrink — run `node scripts/ratchet.mjs --update`:\n');
  shrinks.forEach((text) => console.error(text));
  console.error('');
  process.exit(1);
}

console.log(
  `✓ ratchet: ${guards.length} guard(s) at their floor in ${rel(baselinePath)} ` +
    `(${totalFindings} baselined finding(s), ${totalEscapes} line escape(s)).`,
);
