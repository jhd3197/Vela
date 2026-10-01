/**
 * Snap layouts and the zones a drag can reach, checked without a browser.
 *
 * Every layout has to tile the screen exactly — no cell overlapping another,
 * nothing left over — with the same gutter the split leaves for its divider.
 * And a drag has to mean one thing at each place: an edge is a half, the top is
 * maximize, a corner is a quarter, and the middle of the desk is nothing.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import {
  CORNER,
  LAYOUTS,
  TOP_REACH,
  cellBounds,
  cellById,
  snapZone,
  zoneBounds,
} from '../web/src/desktops/layouts.js';
import { GUTTER, SNAP_EDGE } from '../web/src/desktops/snap.js';
import { MIN_WIDTH } from '../web/src/desktops/window-state.js';

const AREA = { width: 1200, height: 800 };

describe('the layouts', () => {
  test('each one covers the whole screen exactly once', () => {
    for (const layout of LAYOUTS) {
      const area = layout.cells.reduce((sum, cell) => sum + cell.w * cell.h, 0);
      assert.ok(Math.abs(area - 1) < 0.001, `${layout.id} covers ${area} of the screen`);
      for (const a of layout.cells) {
        for (const b of layout.cells) {
          if (a === b) continue;
          const apart =
            a.x + a.w <= b.x + 0.001 ||
            b.x + b.w <= a.x + 0.001 ||
            a.y + a.h <= b.y + 0.001 ||
            b.y + b.h <= a.y + 0.001;
          assert.ok(apart, `${layout.id}: ${a.id} overlaps ${b.id}`);
        }
      }
    }
  });

  test('every cell has a name to be announced by', () => {
    for (const layout of LAYOUTS) {
      assert.ok(layout.name);
      for (const cell of layout.cells) assert.ok(cell.name, `${layout.id}/${cell.id}`);
    }
  });

  test('only the halves are the split view', () => {
    const panes = LAYOUTS.flatMap((layout) => layout.cells.filter((cell) => cell.pane));
    assert.deepEqual(
      panes.map((cell) => cell.pane),
      ['left', 'right'],
    );
  });

  test('the same quarter in two layouts is one cell', () => {
    assert.equal(cellById('top-right').name, 'Top right quarter');
    assert.equal(cellById('no-such-cell'), null);
  });
});

describe('where a cell is on screen', () => {
  test('outer edges are flush, inner edges leave the gutter', () => {
    const left = cellBounds(cellById('left-third'), AREA);
    const right = cellBounds(cellById('right-two-thirds'), AREA);
    assert.equal(left.x, 0);
    assert.equal(left.y, 0);
    assert.equal(left.height, AREA.height);
    assert.equal(right.x + right.width, AREA.width);
    assert.equal(right.x - (left.x + left.width), GUTTER);
  });

  test('quarters leave the gutter both ways', () => {
    const topLeft = cellBounds(cellById('top-left'), AREA);
    const bottomRight = cellBounds(cellById('bottom-right'), AREA);
    assert.equal(bottomRight.x - (topLeft.x + topLeft.width), GUTTER);
    assert.equal(bottomRight.y - (topLeft.y + topLeft.height), GUTTER);
    assert.equal(bottomRight.x + bottomRight.width, AREA.width);
    assert.equal(bottomRight.y + bottomRight.height, AREA.height);
  });

  test('a cell is never smaller than a window may be', () => {
    const tiny = cellBounds(cellById('first-third'), { width: 300, height: 200 });
    assert.ok(tiny.width >= MIN_WIDTH);
  });

  test('no area, no place', () => {
    assert.equal(cellBounds(cellById('left'), { width: 0, height: 0 }), null);
    assert.equal(cellBounds(null, AREA), null);
  });
});

describe('what a drag is reaching for', () => {
  test('the side edges are halves', () => {
    assert.equal(snapZone({ x: 4, y: 400 }, AREA), 'left');
    assert.equal(snapZone({ x: AREA.width - 4, y: 400 }, AREA), 'right');
    assert.equal(snapZone({ x: SNAP_EDGE, y: 400 }, AREA), 'left');
    assert.equal(snapZone({ x: SNAP_EDGE + 1, y: 400 }, AREA), null);
  });

  test('the corners are quarters', () => {
    assert.equal(snapZone({ x: 4, y: 4 }, AREA), 'top-left');
    assert.equal(snapZone({ x: AREA.width - 4, y: 4 }, AREA), 'top-right');
    assert.equal(snapZone({ x: 4, y: AREA.height - 4 }, AREA), 'bottom-left');
    assert.equal(snapZone({ x: AREA.width - 4, y: AREA.height - 4 }, AREA), 'bottom-right');
    assert.equal(snapZone({ x: 4, y: CORNER + 1 }, AREA), 'left', 'below the corner is the edge');
  });

  test('the top, including a little of the bar above it, is maximize', () => {
    assert.equal(snapZone({ x: 600, y: 0 }, AREA), 'top');
    assert.equal(snapZone({ x: 600, y: -20 }, AREA), 'top');
    assert.equal(snapZone({ x: 600, y: -TOP_REACH - 1 }, AREA), null);
    assert.equal(snapZone({ x: 600, y: 2 }, AREA), null, 'just under the top is not the top');
  });

  test('the middle of the desk, and anywhere far outside it, is nothing', () => {
    assert.equal(snapZone({ x: 600, y: 400 }, AREA), null);
    assert.equal(snapZone({ x: 600, y: AREA.height + 10 }, AREA), null);
    assert.equal(snapZone({ x: 600, y: 400 }, { width: 0, height: 0 }), null);
    assert.equal(snapZone(null, AREA), null);
  });

  test('each zone previews what dropping there would do', () => {
    assert.deepEqual(zoneBounds('top', AREA), { x: 0, y: 0, width: 1200, height: 800 });
    const left = zoneBounds('left', AREA);
    const right = zoneBounds('right', AREA);
    assert.equal(left.width + right.width + GUTTER, AREA.width);
    assert.deepEqual(zoneBounds('top-left', AREA), cellBounds(cellById('top-left'), AREA));
    assert.equal(zoneBounds(null, AREA), null);
  });
});
