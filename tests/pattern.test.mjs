import assert from 'node:assert/strict';
import test from 'node:test';
import { appendDot, midpoint, normalizePattern, samePattern } from '../web/src/pattern.js';

// These are the same cases `tests/test_security.py` pins for
// `vela/access.py:normalize_pattern`. The server is authoritative; this check
// exists so the drawing surface cannot drift away from it and offer a pattern
// the engine would refuse.

test('a straight move over an unvisited dot picks that dot up', () => {
  assert.deepEqual(normalizePattern([0, 2, 6, 8]), [0, 1, 2, 4, 6, 7, 8]);
  assert.deepEqual(normalizePattern([0, 1, 2, 5, 8]), [0, 1, 2, 5, 8]);
  // The centre is already used, so 3 -> 5 is allowed to jump over it.
  assert.deepEqual(normalizePattern([1, 4, 3, 5]), [1, 4, 3, 5]);
  // A knight's move crosses no dot.
  assert.deepEqual(normalizePattern([0, 5, 6, 1]), [0, 5, 6, 1]);
  assert.equal(midpoint(0, 8), 4);
  assert.equal(midpoint(0, 5), null);
});

test('short, repeated and out-of-range patterns are refused', () => {
  for (const bad of [[0, 2], [6, 2], [0, 1, 2, 1], [0, 1, 2, 9], [0], [], 'abc', [0, 1, 2, 3.5]])
    assert.equal(normalizePattern(bad), null, JSON.stringify(bad));
});

test('adding a dot while drawing matches what will be submitted', () => {
  assert.deepEqual(appendDot([0], 2), [0, 1, 2]);
  // A dot already on the trail is ignored, so a finger passing back over it
  // and a stray click after a drag both change nothing.
  assert.deepEqual(appendDot([0, 1, 2], 1), [0, 1, 2]);
  assert.deepEqual(appendDot([], 4), [4]);
});

test('two drawings of the same shape compare equal', () => {
  assert.equal(samePattern([0, 2, 5, 8], [0, 1, 2, 5, 8]), true);
  assert.equal(samePattern([0, 1, 2, 5, 8], [0, 1, 2, 5, 7]), false);
  assert.equal(samePattern([0, 2], [0, 2]), false);
});
