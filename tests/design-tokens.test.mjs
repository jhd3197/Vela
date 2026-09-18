// The token tables and the derived recipe: that the whitelist and the alias
// table agree with each other, that deriving is deterministic, and that the
// stock theme the sheet is generated from clears its own contrast gate.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { contrast, flatten } from '../web/src/design/color.js';
import {
  ALIASES,
  CANONICAL,
  CANONICAL_TOKENS,
  CONTRAST_PAIRS,
  GROUP_TYPES,
  RAMPED_ROLES,
  RAMP_STEPS,
  RAMP_STOPS,
  SCALES,
  SCHEMA_VERSION,
  SWATCH_TOKENS,
  TOKEN_GROUP,
  checkContrast,
  derive,
} from '../web/src/design/tokens.js';
import { sheet, whitelist } from '../web/scripts/build-tokens.mjs';

const stock = JSON.parse(
  readFileSync(new URL('../web/src/design/theme.vela.json', import.meta.url)),
);

test('the canonical list is a set, and every group declares a value type', () => {
  assert.equal(new Set(CANONICAL_TOKENS).size, CANONICAL_TOKENS.length, 'a token is listed twice');
  for (const name of CANONICAL_TOKENS) {
    assert.match(name, /^--[a-z][a-z0-9-]*$/, `${name} is not a custom property name`);
    assert.ok(GROUP_TYPES[TOKEN_GROUP[name]], `${name} has no value type`);
  }
  assert.deepEqual(Object.keys(CANONICAL).sort(), Object.keys(GROUP_TYPES).sort());
});

test('every legacy alias maps from exactly one canonical token', () => {
  for (const [alias, canonical] of Object.entries(ALIASES)) {
    assert.ok(CANONICAL_TOKENS.includes(canonical), `${alias} maps to unknown ${canonical}`);
    assert.ok(!CANONICAL_TOKENS.includes(alias), `${alias} is both an alias and canonical`);
  }
  // An alias may not point at another alias: expansion is one hop, in the
  // generator and in the runtime applier alike.
  for (const canonical of Object.values(ALIASES)) assert.ok(!(canonical in ALIASES));
});

test('the ramp stops are nine, ordered light to dark, inside 0 and 1', () => {
  assert.equal(RAMP_STOPS.length, 9);
  assert.equal(RAMP_STEPS.length, 9);
  for (const [index, stop] of RAMP_STOPS.entries()) {
    assert.ok(stop > 0 && stop < 1, `stop ${index} is outside the range`);
    if (index) assert.ok(stop < RAMP_STOPS[index - 1], `stop ${index} is not darker`);
  }
});

test('deriving is deterministic and never mutates its input', () => {
  const input = { ...stock.tokens.light };
  const once = derive(input, 'light');
  const twice = derive(input, 'light');
  assert.deepEqual(once, twice, 'two derivations of the same theme disagreed');
  assert.deepEqual(input, stock.tokens.light, 'derive() wrote into the theme it was given');
});

test('deriving produces every step, tint and alias the stylesheet reads', () => {
  for (const base of stock.bases) {
    const derived = derive(stock.tokens[base], base);
    for (const role of RAMPED_ROLES) {
      for (const step of RAMP_STEPS) {
        assert.match(
          derived[`--${role}-${step}`] || '',
          /^#[0-9a-f]{6}$/,
          `${base} ${role} ${step}`,
        );
      }
    }
    for (const name of [
      '--accent-soft',
      '--accent-strong',
      '--accent-line',
      '--green-soft',
      '--amber-soft',
      '--red-soft',
      '--nav-active-text',
      '--nav-active-bg',
      '--nav-active-bar',
      ...Object.keys(ALIASES),
    ]) {
      assert.ok(derived[name], `${base} is missing ${name}`);
    }
    for (const [alias, canonical] of Object.entries(ALIASES)) {
      assert.equal(
        derived[alias],
        derived[canonical],
        `${base}: ${alias} drifted from ${canonical}`,
      );
    }
  }
});

test('--accent-strong is the first step that clears 4.5 on the accent tint', () => {
  for (const base of stock.bases) {
    const derived = derive(stock.tokens[base], base);
    // The tint is translucent, so it is measured over the ground it is drawn
    // on -- the darkest surface it can sit on, which is what the step has to
    // clear for the pill to be legible everywhere it appears.
    const tint = flatten(derived['--accent-soft'], derived['--bg']);
    assert.ok(
      contrast(derived['--accent-strong'], tint) >= 4.5,
      `${base}: --accent-strong is not legible on its own tint (${tint})`,
    );
  }
});

test('the stock theme passes its own contrast gate in both bases', () => {
  for (const base of stock.bases) {
    const failures = checkContrast(derive(stock.tokens[base], base));
    assert.deepEqual(failures, [], `${base} base failed: ${JSON.stringify(failures)}`);
  }
  assert.ok(CONTRAST_PAIRS.length >= 16, 'the gate stopped measuring most of the pairs');
});

test('the stock theme sets canonical tokens and nothing else', () => {
  assert.equal(stock.schema_version, SCHEMA_VERSION);
  assert.deepEqual(stock.bases, ['light', 'dark']);
  for (const base of stock.bases) {
    const names = Object.keys(stock.tokens[base]);
    assert.deepEqual(
      names.filter((name) => !CANONICAL_TOKENS.includes(name)),
      [],
      `${base} sets something that is not canonical`,
    );
    assert.deepEqual(
      CANONICAL_TOKENS.filter((name) => !names.includes(name)),
      [],
      `${base} leaves a canonical token unset`,
    );
  }
  for (const token of SWATCH_TOKENS) assert.ok(CANONICAL_TOKENS.includes(token), token);
});

test('the committed token sheet is what the generator writes', () => {
  const committed = readFileSync(
    new URL('../web/src/styles/_tokens.scss', import.meta.url),
    'utf8',
  );
  assert.equal(
    committed.replace(/\r\n/g, '\n'),
    sheet(),
    '_tokens.scss has drifted — run `node web/scripts/build-tokens.mjs`.',
  );
});

test('the whitelist the server reads is the one the tables declare', () => {
  const exported = JSON.parse(
    readFileSync(new URL('../vela/assets/theme-tokens.json', import.meta.url), 'utf8'),
  );
  assert.deepEqual(exported, whitelist(), 'theme-tokens.json is stale — run the generator.');
  assert.deepEqual(Object.keys(exported.tokens).sort(), [...CANONICAL_TOKENS].sort());
});

test('spacing, type and motion are system constants, not theme data', () => {
  for (const group of Object.values(SCALES)) {
    for (const name of Object.keys(group)) {
      assert.ok(!CANONICAL_TOKENS.includes(name), `${name} is a scale and must not be theme data`);
    }
  }
  assert.equal(Object.keys(SCALES.space).length, 8);
  assert.equal(SCALES.motion['--motion-base'], '200ms');
});
