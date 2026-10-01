// Snap layouts: the ways a screen can be divided, as data.
//
// A layout is a set of cells, each a fraction of the work area. Choosing a cell
// for a window puts the window there. The two halves are the split view that
// already exists — the pair with a shared divider, which keeps its empty-pane
// picker and its keyboard-movable divider — and every other cell places the
// window as an ordinary floating window at that rectangle, which it remembers
// like any other place it has been put.
//
// No React and no DOM, like `snap.js` and `window-state.js`: what a drag into
// a corner means, and where a third of the screen ends, are answers a test can
// check without a pointer.
import { MIN_HEIGHT, MIN_WIDTH, maximizedBounds } from './window-state.js';
import { GUTTER, SNAP_EDGE, snapTargetFor } from './snap.js';

const THIRD = 1 / 3;

/** Every layout offered, in the order the picker shows them. */
export const LAYOUTS = [
  {
    id: 'halves',
    name: 'Two halves',
    cells: [
      { id: 'left', name: 'Left half', x: 0, y: 0, w: 0.5, h: 1, pane: 'left' },
      { id: 'right', name: 'Right half', x: 0.5, y: 0, w: 0.5, h: 1, pane: 'right' },
    ],
  },
  {
    id: 'wide-left',
    name: 'Two thirds and a third',
    cells: [
      { id: 'left-two-thirds', name: 'Left two thirds', x: 0, y: 0, w: 2 * THIRD, h: 1 },
      { id: 'right-third', name: 'Right third', x: 2 * THIRD, y: 0, w: THIRD, h: 1 },
    ],
  },
  {
    id: 'wide-right',
    name: 'A third and two thirds',
    cells: [
      { id: 'left-third', name: 'Left third', x: 0, y: 0, w: THIRD, h: 1 },
      { id: 'right-two-thirds', name: 'Right two thirds', x: THIRD, y: 0, w: 2 * THIRD, h: 1 },
    ],
  },
  {
    id: 'thirds',
    name: 'Three columns',
    cells: [
      { id: 'first-third', name: 'Left column', x: 0, y: 0, w: THIRD, h: 1 },
      { id: 'middle-third', name: 'Middle column', x: THIRD, y: 0, w: THIRD, h: 1 },
      { id: 'last-third', name: 'Right column', x: 2 * THIRD, y: 0, w: THIRD, h: 1 },
    ],
  },
  {
    id: 'half-and-quarters',
    name: 'A half and two quarters',
    cells: [
      { id: 'half-left', name: 'Left half', x: 0, y: 0, w: 0.5, h: 1 },
      { id: 'top-right', name: 'Top right quarter', x: 0.5, y: 0, w: 0.5, h: 0.5 },
      { id: 'bottom-right', name: 'Bottom right quarter', x: 0.5, y: 0.5, w: 0.5, h: 0.5 },
    ],
  },
  {
    id: 'quarters',
    name: 'Four quarters',
    cells: [
      { id: 'top-left', name: 'Top left quarter', x: 0, y: 0, w: 0.5, h: 0.5 },
      { id: 'top-right', name: 'Top right quarter', x: 0.5, y: 0, w: 0.5, h: 0.5 },
      { id: 'bottom-left', name: 'Bottom left quarter', x: 0, y: 0.5, w: 0.5, h: 0.5 },
      { id: 'bottom-right', name: 'Bottom right quarter', x: 0.5, y: 0.5, w: 0.5, h: 0.5 },
    ],
  },
];

/** One cell, from any layout, by its id. The same quarter in two layouts is one cell. */
export function cellById(id) {
  for (const layout of LAYOUTS) {
    const found = layout.cells.find((cell) => cell.id === id);
    if (found) return found;
  }
  return null;
}

const near = (a, b) => Math.abs(a - b) < 0.001;

/**
 * Where a cell is on screen, in work-area pixels.
 *
 * Cells share the same gutter the split leaves for its divider, taken half from
 * each side of an inner edge, so two windows side by side never touch and a
 * window in a cell lines up with one in a split pane. The outer edges are
 * flush, like a maximized window's.
 */
export function cellBounds(cell, area, gutter = GUTTER) {
  if (!cell || !area?.width || !area?.height) return null;
  const half = gutter / 2;
  const left = cell.x * area.width + (near(cell.x, 0) ? 0 : half);
  const right = (cell.x + cell.w) * area.width - (near(cell.x + cell.w, 1) ? 0 : half);
  const top = cell.y * area.height + (near(cell.y, 0) ? 0 : half);
  const bottom = (cell.y + cell.h) * area.height - (near(cell.y + cell.h, 1) ? 0 : half);
  return {
    x: Math.round(left),
    y: Math.round(top),
    width: Math.max(MIN_WIDTH, Math.round(right - left)),
    height: Math.max(MIN_HEIGHT, Math.round(bottom - top)),
  };
}

/** How tall a corner's zone is along the side edges, beyond the edge distance itself. */
export const CORNER = 120;

/** How far above the work area a pointer may be and still be reaching for the top. */
export const TOP_REACH = 64;

/**
 * What a pointer during a title-bar drag is reaching for, or null.
 *
 * The left and right edges are halves, as they always were. The top edge is
 * maximize — including a little way into the bar above the desk, because a
 * window being dragged up has its pointer on its own title bar, and that bar
 * reaches the top of the desk before the pointer does. A corner is that
 * quarter of the screen. Anything further outside the work area is nothing.
 */
export function snapZone(point, area) {
  if (!area?.width || !area?.height) return null;
  if (!Number.isFinite(point?.x) || !Number.isFinite(point?.y)) return null;
  const { x, y } = point;
  if (y < -TOP_REACH || y > area.height || x < -SNAP_EDGE || x > area.width + SNAP_EDGE) {
    return null;
  }
  // The sides are the split's own rule; a pointer a little above the desk is
  // measured as if it were at its top.
  const side = snapTargetFor({ x, y: Math.max(0, y) }, area);
  const corner = Math.min(CORNER, area.height / 4);
  if (side) {
    if (y <= corner) return `top-${side}`;
    if (y >= area.height - corner) return `bottom-${side}`;
    return side;
  }
  if (y <= 0) return 'top';
  return null;
}

/** The rectangle a zone's preview draws, in work-area pixels. */
export function zoneBounds(zone, area, { ratio = 0.5 } = {}) {
  if (!zone) return null;
  if (zone === 'top') return maximizedBounds(area);
  if (zone === 'left' || zone === 'right') {
    const usable = Math.max(0, area.width - GUTTER);
    const primary = Math.round(usable * ratio);
    return zone === 'left'
      ? { x: 0, y: 0, width: primary, height: area.height }
      : { x: primary + GUTTER, y: 0, width: area.width - primary - GUTTER, height: area.height };
  }
  return cellBounds(cellById(zone), area);
}
