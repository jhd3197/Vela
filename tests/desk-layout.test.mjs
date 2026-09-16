import assert from 'node:assert/strict';
import test from 'node:test';
import {
  clampToCols,
  compact,
  findFreeSpot,
  nextWidgetId,
  overlaps,
  pushDown,
} from '../web/src/desk/grid/layout.js';

// The board geometry is the one piece of the desk that both sides implement:
// these rules are mirrored in `vela/desk.py`, which validates a saved board
// before it is written. Keep the two in step.

const at = (i, x, y, w = 2, h = 1) => ({ i, type: 'clock', x, y, w, h });

test('overlaps is symmetric and a widget never overlaps itself', () => {
  const a = at('w1', 0, 0, 2, 2);
  const b = at('w2', 1, 1, 2, 2);
  assert.equal(overlaps(a, b), true);
  assert.equal(overlaps(b, a), true);
  assert.equal(overlaps(a, { ...a }), false, 'the same id is the same widget');
  // Touching edges are not an overlap: w1 ends at x=2, w3 starts there.
  assert.equal(overlaps(a, at('w3', 2, 0, 2, 2)), false);
  assert.equal(overlaps(a, at('w4', 0, 2, 2, 2)), false);
});

test('compact floats a widget up past a gap', () => {
  const board = [at('w1', 0, 0), at('w2', 0, 4), at('w3', 2, 7)];
  assert.deepEqual(
    compact(board).map((widget) => [widget.i, widget.x, widget.y]),
    [
      ['w1', 0, 0],
      ['w2', 0, 1],
      ['w3', 2, 0],
    ],
  );
  // The input array and its objects are left alone.
  assert.equal(board[1].y, 4);
});

test('pushDown chains two collisions', () => {
  const board = [at('w1', 0, 0), at('w2', 0, 1), at('w3', 0, 2)];
  // w1 grows to two rows, so w2 has to move down, which moves w3 down too.
  const next = pushDown(board, { ...board[0], h: 2 });
  assert.deepEqual(
    next.map((widget) => [widget.i, widget.y, widget.h]),
    [
      ['w1', 0, 2],
      ['w2', 2, 1],
      ['w3', 3, 1],
    ],
  );
});

test('findFreeSpot fills row 0 left to right, then wraps', () => {
  const cols = 6;
  assert.deepEqual(findFreeSpot([], 2, 1, cols), { x: 0, y: 0 });
  const one = [at('w1', 0, 0)];
  assert.deepEqual(findFreeSpot(one, 2, 1, cols), { x: 2, y: 0 });
  const row = [at('w1', 0, 0), at('w2', 2, 0), at('w3', 4, 0)];
  assert.deepEqual(findFreeSpot(row, 2, 1, cols), { x: 0, y: 1 });
  // A 2-column board only ever has one slot per row.
  assert.deepEqual(findFreeSpot([at('w1', 0, 0)], 2, 1, 2), { x: 0, y: 1 });
  // A widget wider than the board still lands somewhere rather than nowhere.
  assert.deepEqual(findFreeSpot([], 6, 1, 2), { x: 0, y: 0 });
});

test('nextWidgetId skips ids already on the board', () => {
  assert.equal(nextWidgetId([]), 'w1');
  assert.equal(nextWidgetId([at('w1', 0, 0)]), 'w2');
  assert.equal(nextWidgetId([at('w1', 0, 0), at('w3', 2, 0)]), 'w2');
  assert.equal(nextWidgetId([at('w1', 0, 0), at('w2', 2, 0)]), 'w3');
});

test('clampToCols shrinks a six-wide widget onto a two-column board', () => {
  const board = [at('w1', 0, 0, 6, 2), at('w2', 4, 2, 2, 1)];
  const phone = clampToCols(board, 2);
  assert.deepEqual(
    phone.map((widget) => [widget.i, widget.x, widget.y, widget.w]),
    [
      ['w1', 0, 0, 2],
      ['w2', 0, 2, 2],
    ],
  );
  // Nothing sticks out past the right edge, and nothing overlaps.
  for (const widget of phone) assert.ok(widget.x + widget.w <= 2);
  for (const a of phone) for (const b of phone) assert.equal(overlaps(a, b), false);
  // A board that already fits is only compacted.
  assert.deepEqual(clampToCols([at('w1', 0, 3, 2, 1)], 6)[0].y, 0);
});
