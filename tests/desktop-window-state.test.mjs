/**
 * Window geometry, checked without a browser.
 *
 * These are the rules that have to keep holding after somebody rotates a
 * tablet, zooms to 200%, drags the browser to a smaller screen or opens a
 * layout saved on a monitor they no longer own. The arithmetic lives apart from
 * React for exactly that reason: it can be asked all of those questions here,
 * in milliseconds, instead of only in a real browser at one size.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import {
  clampBounds,
  defaultBounds,
  dividerRatio,
  isBounds,
  maximizedBounds,
  minimizePatch,
  MIN_HEIGHT,
  MIN_WIDTH,
  moveBounds,
  placeView,
  resizeBounds,
  restorePatch,
  splitBounds,
  stackOrder,
  TITLE_BAR,
  workArea,
} from '../web/src/desktops/window-state.js';

const AREA = { width: 1200, height: 800 };
const view = (over = {}) => ({ id: 'v1', window: { minimized: false, stack: 0 }, ...over });

describe('the work area', () => {
  test('is measured, not assumed', () => {
    assert.deepEqual(workArea({ width: 1200.4, height: 799.6 }), { width: 1200, height: 800 });
  });

  test('a missing measurement is zero rather than a guess', () => {
    assert.deepEqual(workArea(null), { width: 0, height: 0 });
    assert.deepEqual(workArea({ width: NaN, height: 10 }), { width: 0, height: 0 });
  });
});

describe('clamping', () => {
  test('leaves a window that already fits exactly where it is', () => {
    const bounds = { x: 100, y: 80, width: 600, height: 400 };
    assert.deepEqual(clampBounds(bounds, AREA), bounds);
  });

  test('never lets the title bar go above the top', () => {
    const { y } = clampBounds({ x: 10, y: -300, width: 600, height: 400 }, AREA);
    assert.equal(y, 0, 'there is nothing above a title bar to grab it by');
  });

  test('keeps part of a window reachable when it hangs off an edge', () => {
    const left = clampBounds({ x: -5000, y: 10, width: 600, height: 400 }, AREA);
    assert.ok(left.x + left.width > 0, `something is still on screen: ${JSON.stringify(left)}`);
    const right = clampBounds({ x: 5000, y: 10, width: 600, height: 400 }, AREA);
    assert.ok(right.x < AREA.width, `something is still on screen: ${JSON.stringify(right)}`);
  });

  test('shrinks a window only when the area cannot hold it', () => {
    const small = { width: 400, height: 300 };
    const fitted = clampBounds({ x: 0, y: 0, width: 900, height: 700 }, small);
    assert.deepEqual(fitted, { x: 0, y: 0, width: 400, height: 300 });
  });

  test('never goes below the minimum, even in an area smaller than one', () => {
    const tiny = clampBounds({ x: 0, y: 0, width: 300, height: 200 }, { width: 50, height: 40 });
    assert.equal(tiny.width, MIN_WIDTH);
    assert.equal(tiny.height, MIN_HEIGHT);
  });

  test('refuses something that is not a rectangle', () => {
    assert.equal(clampBounds(null, AREA), null);
    assert.equal(clampBounds({ x: 0, y: 0, width: '600', height: 400 }, AREA), null);
    assert.equal(clampBounds({ x: 0, y: 0, width: NaN, height: 400 }, AREA), null);
    assert.equal(isBounds({ x: 0, y: 0, width: 1, height: 1 }), true);
  });
});

describe('opening a window with no saved place', () => {
  test('centres it and leaves room around it', () => {
    const bounds = defaultBounds(AREA);
    assert.ok(bounds.width < AREA.width && bounds.height < AREA.height);
    assert.equal(bounds.x, Math.round((AREA.width - bounds.width) / 2));
  });

  test('steps each new one clear of the last, and wraps rather than marching off', () => {
    const first = defaultBounds(AREA, 0);
    const second = defaultBounds(AREA, 1);
    assert.ok(second.x > first.x && second.y > first.y, 'the second is not on top of the first');
    const far = defaultBounds(AREA, 40);
    assert.ok(far.x < AREA.width && far.y < AREA.height, 'and the fortieth is still on screen');
  });

  test('fits an area barely larger than the minimum', () => {
    const bounds = defaultBounds({ width: MIN_WIDTH, height: MIN_HEIGHT });
    assert.equal(bounds.width, MIN_WIDTH);
    assert.equal(bounds.height, MIN_HEIGHT);
  });
});

describe('arrangements', () => {
  test('maximized fills the work area exactly', () => {
    assert.deepEqual(maximizedBounds(AREA), { x: 0, y: 0, width: 1200, height: 800 });
  });

  test('a split takes the gutter out of the panes rather than over them', () => {
    const left = splitBounds(AREA, 0.5, 'primary', 8);
    const right = splitBounds(AREA, 0.5, 'secondary', 8);
    assert.equal(left.x, 0);
    assert.equal(right.x, left.width + 8, 'the divider sits between them');
    assert.equal(left.width + right.width + 8, AREA.width, 'and nothing is covered');
  });

  test('neither pane can be dragged away to nothing', () => {
    const left = splitBounds(AREA, 0.001, 'primary');
    const right = splitBounds(AREA, 0.999, 'secondary');
    assert.ok(left.width >= MIN_WIDTH);
    assert.ok(right.width >= MIN_WIDTH);
  });

  test('the divider ratio stays off both ends', () => {
    assert.equal(dividerRatio(-500, AREA), 0.2);
    assert.equal(dividerRatio(5000, AREA), 0.8);
    assert.equal(dividerRatio(600, AREA), 0.5);
    assert.equal(dividerRatio(600, { width: 0 }), 0.5, 'an unmeasured area is not a divide by zero');
  });
});

describe('placing a view', () => {
  const layout = {
    arrangement: 'floating',
    maximizedView: null,
    primaryView: null,
    secondaryView: null,
    dividerRatio: 0.5,
  };

  test('a minimized window is not drawn at all', () => {
    const hidden = view({ window: { minimized: true, bounds: { x: 0, y: 0, width: 600, height: 400 } } });
    assert.equal(placeView(hidden, { layout, area: AREA }), null, 'not drawn, rather than drawn off-screen');
  });

  test('a floating window uses its saved place, clamped', () => {
    const saved = view({ window: { bounds: { x: 9000, y: 40, width: 600, height: 400 } } });
    const placed = placeView(saved, { layout, area: AREA });
    assert.ok(placed.x < AREA.width);
    assert.equal(placed.width, 600, 'it is moved, not resized');
  });

  test('only the maximized view is drawn while one is maximized', () => {
    const max = { ...layout, arrangement: 'maximized', maximizedView: 'v1' };
    assert.deepEqual(placeView(view(), { layout: max, area: AREA }), maximizedBounds(AREA));
    assert.equal(placeView(view({ id: 'v2' }), { layout: max, area: AREA }), null);
  });

  test('a split draws its two members and nothing else', () => {
    const split = { ...layout, arrangement: 'split', primaryView: 'v1', secondaryView: 'v2' };
    assert.ok(placeView(view(), { layout: split, area: AREA }));
    assert.ok(placeView(view({ id: 'v2' }), { layout: split, area: AREA }));
    assert.equal(placeView(view({ id: 'v3' }), { layout: split, area: AREA }), null);
  });

  test('a split with one pane empty still draws the other', () => {
    const half = { ...layout, arrangement: 'split', primaryView: 'v1', secondaryView: null };
    assert.ok(placeView(view(), { layout: half, area: AREA }));
  });
});

describe('minimize and restore', () => {
  test('minimizing remembers where the window was and moves nothing', () => {
    const open = view({ window: { bounds: { x: 120, y: 90, width: 700, height: 500 } } });
    const patch = minimizePatch(open, AREA);
    assert.equal(patch.minimized, true);
    assert.deepEqual(patch.restoreBounds, { x: 120, y: 90, width: 700, height: 500 });
    assert.ok(!('bounds' in patch), 'minimizing is not moving');
  });

  test('restoring puts it back where it was and brings it forward', () => {
    const hidden = view({
      window: { minimized: true, restoreBounds: { x: 120, y: 90, width: 700, height: 500 } },
    });
    const patch = restorePatch(hidden, AREA);
    assert.equal(patch.minimized, false);
    assert.equal(patch.raise, true);
    assert.deepEqual(patch.bounds, { x: 120, y: 90, width: 700, height: 500 });
  });

  test('a place from a screen that is no longer attached becomes a sensible one', () => {
    const hidden = view({
      window: { minimized: true, restoreBounds: { x: 3000, y: 2000, width: 2400, height: 1600 } },
    });
    const patch = restorePatch(hidden, AREA);
    assert.ok(patch.bounds.x < AREA.width && patch.bounds.y < AREA.height);
    assert.ok(patch.bounds.width <= AREA.width && patch.bounds.height <= AREA.height);
  });

  test('a window that was never placed gets a default rather than nothing', () => {
    const patch = restorePatch(view({ window: { minimized: true } }), AREA, 2);
    assert.deepEqual(patch.bounds, defaultBounds(AREA, 2));
  });
});

describe('dragging and resizing', () => {
  const bounds = { x: 100, y: 100, width: 600, height: 400 };

  test('dragging moves the whole window', () => {
    assert.deepEqual(moveBounds(bounds, { x: 50, y: -40 }, AREA), {
      x: 150,
      y: 60,
      width: 600,
      height: 400,
    });
  });

  test('dragging cannot put the title bar out of reach', () => {
    const up = moveBounds(bounds, { x: 0, y: -5000 }, AREA);
    assert.equal(up.y, 0);
    const down = moveBounds(bounds, { x: 0, y: 5000 }, AREA);
    assert.ok(down.y <= AREA.height - TITLE_BAR);
  });

  test('pulling an edge moves that edge', () => {
    assert.deepEqual(resizeBounds(bounds, { x: 100, y: 50 }, 'se', AREA), {
      x: 100,
      y: 100,
      width: 700,
      height: 450,
    });
    const west = resizeBounds(bounds, { x: -80, y: 0 }, 'w', AREA);
    assert.equal(west.x, 20);
    assert.equal(west.width, 680, 'the right edge stayed put');
  });

  test('an edge pulled past the minimum stops the edge, not the window', () => {
    const west = resizeBounds(bounds, { x: 5000, y: 0 }, 'w', AREA);
    assert.equal(west.width, MIN_WIDTH);
    assert.equal(west.x, 100 + 600 - MIN_WIDTH, 'the far edge has not moved');
    const north = resizeBounds(bounds, { x: 0, y: 5000 }, 'n', AREA);
    assert.equal(north.height, MIN_HEIGHT);
  });
});

describe('stacking', () => {
  test('is back to front, and does not disturb the caller', () => {
    const views = [
      { id: 'a', window: { stack: 3 } },
      { id: 'b', window: { stack: 1 } },
      { id: 'c', window: {} },
    ];
    assert.deepEqual(
      stackOrder(views).map((entry) => entry.id),
      ['c', 'b', 'a'],
    );
    assert.deepEqual(
      views.map((entry) => entry.id),
      ['a', 'b', 'c'],
    );
  });
});
