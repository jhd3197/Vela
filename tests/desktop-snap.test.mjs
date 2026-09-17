/**
 * Putting a window into half the screen, checked without a browser.
 *
 * The interesting questions here are not about pixels; they are about what a
 * drag near an edge *means*, and those answers have to hold after somebody
 * snaps the same window twice, closes one member of a split, minimizes the
 * other and comes back an hour later.
 *
 * Two of them are the reason this file exists at all: a split never ends up
 * showing the same window twice, and a pane whose view went away stays a pane
 * rather than quietly collapsing and taking the slot somebody meant to restore
 * into.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { SPLIT_MIN_WIDTH, placeView } from '../web/src/desktops/window-state.js';

import {
  exitPatch,
  freeSlotFor,
  GUTTER,
  MAX_RATIO,
  MIN_RATIO,
  panes,
  previewBounds,
  ratioAt,
  SNAP_EDGE,
  snapPatch,
  snapTargetFor,
  stepRatio,
  swapPatch,
  vacatePatch,
} from '../web/src/desktops/snap.js';

const AREA = { width: 1200, height: 800 };
const floating = { arrangement: 'floating', dividerRatio: 0.5 };
const split = (over = {}) => ({
  arrangement: 'split',
  primaryView: 'a',
  secondaryView: 'b',
  dividerRatio: 0.5,
  ...over,
});

describe('offering a half', () => {
  test('only the left and right edges offer one', () => {
    assert.equal(snapTargetFor({ x: 4, y: 400 }, AREA), 'left');
    assert.equal(snapTargetFor({ x: 1196, y: 400 }, AREA), 'right');
    assert.equal(snapTargetFor({ x: 600, y: 4 }, AREA), null, 'the top is not half-built here');
    assert.equal(snapTargetFor({ x: 600, y: 400 }, AREA), null);
  });

  test('the offer starts exactly at the edge distance and not before', () => {
    assert.equal(snapTargetFor({ x: SNAP_EDGE, y: 400 }, AREA), 'left');
    assert.equal(snapTargetFor({ x: SNAP_EDGE + 1, y: 400 }, AREA), null);
    assert.equal(snapTargetFor({ x: AREA.width - SNAP_EDGE, y: 400 }, AREA), 'right');
    assert.equal(snapTargetFor({ x: AREA.width - SNAP_EDGE - 1, y: 400 }, AREA), null);
  });

  test('a pointer outside the work area offers nothing', () => {
    assert.equal(snapTargetFor({ x: 4, y: -20 }, AREA), null);
    assert.equal(snapTargetFor({ x: 4, y: 900 }, AREA), null);
    assert.equal(snapTargetFor({ x: 4, y: 400 }, { width: 0, height: 0 }), null);
    assert.equal(snapTargetFor(null, AREA), null);
  });

  test('the preview is the pane it would become, gutter and all', () => {
    const left = previewBounds('left', AREA, 0.5);
    const right = previewBounds('right', AREA, 0.5);
    assert.equal(left.x, 0);
    assert.equal(right.x, left.width + GUTTER);
    assert.equal(left.width + right.width + GUTTER, AREA.width);
  });
});

describe('moving a window into a pane', () => {
  test('a floating window becomes one half and leaves the other empty', () => {
    const patch = snapPatch(floating, 'a', 'left');
    assert.equal(patch.arrangement, 'split');
    assert.equal(patch.primaryView, 'a');
    assert.equal(patch.secondaryView, null);
    assert.equal(patch.selectedView, 'a');
  });

  test('the other pane keeps whatever it already had', () => {
    const patch = snapPatch(split({ primaryView: 'a', secondaryView: 'b' }), 'c', 'left');
    assert.equal(patch.primaryView, 'c');
    assert.equal(patch.secondaryView, 'b');
  });

  test('a window moved to the other side is moved, never copied', () => {
    // The failure this prevents: 'a' on the left, dragged to the right, ending
    // up as both halves of a split showing the same window twice.
    const patch = snapPatch(split({ primaryView: 'a', secondaryView: 'b' }), 'a', 'right');
    assert.equal(patch.secondaryView, 'a');
    assert.equal(patch.primaryView, null, 'the side it came from is empty, not a second copy');
  });

  test('snapping a window into the half it is already in changes nothing', () => {
    assert.equal(snapPatch(split(), 'a', 'left'), null);
    assert.equal(snapPatch(split(), 'b', 'right'), null);
  });

  test('an existing divider position survives a snap', () => {
    const patch = snapPatch(split({ dividerRatio: 0.72 }), 'c', 'right');
    assert.equal(patch.dividerRatio, 0.72);
  });
});

describe('leaving a pane', () => {
  test('a closed or minimized member leaves an empty slot behind', () => {
    assert.deepEqual(vacatePatch(split(), 'a'), { primaryView: null });
    assert.deepEqual(vacatePatch(split(), 'b'), { secondaryView: null });
  });

  test('a view that was never in the split leaves it alone', () => {
    assert.equal(vacatePatch(split(), 'c'), null);
    assert.equal(vacatePatch(floating, 'a'), null);
  });

  test('a minimized member goes back to its own slot while it is free', () => {
    assert.equal(freeSlotFor(split({ primaryView: null }), 'a'), 'left');
    assert.equal(freeSlotFor(split({ secondaryView: null }), 'b'), 'right');
  });

  test('and does not evict whatever took its place', () => {
    assert.equal(freeSlotFor(split({ primaryView: 'c' }), 'a'), null);
    assert.equal(freeSlotFor(split(), 'a'), null, 'it is already in one');
    assert.equal(freeSlotFor(floating, 'a'), null);
  });

  test('the panes of a split are always two, named, and possibly empty', () => {
    assert.deepEqual(
      panes(split({ secondaryView: null })).map((pane) => [pane.side, pane.viewId]),
      [
        ['left', 'a'],
        ['right', null],
      ],
    );
    assert.deepEqual(panes(floating), []);
  });
});

describe('swapping and leaving', () => {
  test('swapping exchanges the views and mirrors the divider', () => {
    const patch = swapPatch(split({ dividerRatio: 0.7 }));
    assert.equal(patch.primaryView, 'b');
    assert.equal(patch.secondaryView, 'a');
    // The pane somebody made wider stays the wider pane.
    assert.equal(patch.dividerRatio, 0.3);
  });

  test('swapping an empty side keeps it empty rather than inventing a view', () => {
    const patch = swapPatch(split({ secondaryView: null }));
    assert.equal(patch.primaryView, null);
    assert.equal(patch.secondaryView, 'a');
  });

  test('swapping outside a split does nothing', () => {
    assert.equal(swapPatch(floating), null);
  });

  test('leaving clears both panes and goes back to floating', () => {
    assert.deepEqual(exitPatch(), {
      arrangement: 'floating',
      primaryView: null,
      secondaryView: null,
    });
  });
});

describe('the divider', () => {
  test('a keyboard step moves it and stops at both ends', () => {
    assert.equal(stepRatio(0.5, 1), 0.52);
    assert.equal(stepRatio(0.5, -1), 0.48);
    assert.equal(stepRatio(MIN_RATIO, -1), MIN_RATIO);
    assert.equal(stepRatio(MAX_RATIO, 1), MAX_RATIO);
    assert.equal(stepRatio(undefined, 1), 0.52, 'a missing ratio is half');
  });

  test('a pointer position becomes a ratio inside the usable range', () => {
    assert.equal(ratioAt(600, AREA), 0.5);
    assert.equal(ratioAt(-500, AREA), MIN_RATIO);
    assert.equal(ratioAt(99999, AREA), MAX_RATIO);
    assert.equal(ratioAt(600, { width: 0 }), 0.5);
  });
});

describe('a split too narrow to show as two', () => {
  const view = (id) => ({ id, window: { minimized: false, stack: 0 } });
  const layout = { ...split(), selectedView: 'b' };
  const narrow = { width: SPLIT_MIN_WIDTH - 1, height: 700 };

  test('one pane is drawn, full width, and the other is not drawn at all', () => {
    const front = placeView(view('b'), { layout, area: narrow });
    const behind = placeView(view('a'), { layout, area: narrow });
    assert.deepEqual(front, { x: 0, y: 0, width: narrow.width, height: narrow.height });
    assert.equal(behind, null, 'two 200-pixel columns are not a split screen');
  });

  test('the arrangement somebody made is kept, so a wider screen restores it', () => {
    // Nothing above changed the layout: the same record, drawn wide, is two
    // panes again. Rotating a tablet back is not supposed to lose an
    // arrangement.
    const wide = { width: 1200, height: 700 };
    const left = placeView(view('a'), { layout, area: wide });
    const right = placeView(view('b'), { layout, area: wide });
    assert.ok(left.width > 0 && right.width > 0);
    assert.ok(right.x > left.x);
  });

  test('with nothing selected the pane that has something in it comes forward', () => {
    const half = { ...split({ primaryView: null }), selectedView: null };
    assert.ok(placeView(view('b'), { layout: half, area: narrow }));
    const other = { ...split({ secondaryView: null }), selectedView: null };
    assert.ok(placeView(view('a'), { layout: other, area: narrow }));
  });
});
