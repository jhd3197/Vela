// Pure geometry helpers for the desk widget grid.
//
// Origin: ServerKit `frontend/src/components/dashboard/grid/layout.js` (MIT,
// same owner). Changed here: the board's column count is an argument rather
// than a module constant, because the desk keeps a 6-column desktop board and
// a 2-column phone board side by side, and `clampToCols` moves a board between
// them without inventing a reflow.
//
// A widget instance is `{ i, type, x, y, w, h, cfg }` where x/y are cell
// coordinates and w/h are spans in cells. Everything in here is side-effect
// free and returns new arrays, so callers can drive React state from the
// result.

/** Columns on the desktop board. */
export const DESKTOP_COLS = 6;
/** Columns on the phone board. */
export const PHONE_COLS = 2;
/** Gutter between cells, in px. */
export const GRID_GAP = 16;
/** Height of one row unit, in px. */
export const GRID_ROW = 150;

// findFreeSpot gives up after this many rows rather than looping forever on a
// pathological board.
const MAX_SCAN_ROWS = 200;

/**
 * True when two widget rectangles intersect. A widget never overlaps itself,
 * so the same `i` short-circuits to false.
 */
export function overlaps(a, b) {
  return a.i !== b.i && a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
}

/**
 * Gravity-up compaction: walk the widgets top-to-bottom, left-to-right and
 * float each one as far up as it can go without hitting an already-placed
 * neighbour. Returns a new array of new objects.
 */
export function compact(list) {
  const out = [...list].sort((a, b) => a.y - b.y || a.x - b.x).map((widget) => ({ ...widget }));
  const placed = [];
  out.forEach((widget) => {
    let y = widget.y;
    while (y > 0 && !placed.some((other) => overlaps(other, { ...widget, y: y - 1 }))) y -= 1;
    widget.y = y;
    placed.push(widget);
  });
  return out;
}

/**
 * Apply `moved` to the board, push anything it lands on straight down (and
 * anything those hit, recursively), then re-compact. Returns a new array.
 */
export function pushDown(list, moved) {
  const out = list.map((widget) => (widget.i === moved.i ? { ...moved } : { ...widget }));
  const seen = new Set();
  const walk = (target) => {
    if (!target || seen.has(target.i)) return;
    seen.add(target.i);
    out
      .filter((widget) => overlaps(widget, target))
      .forEach((widget) => {
        widget.y = target.y + target.h;
        walk(widget);
      });
  };
  walk(out.find((widget) => widget.i === moved.i));
  return compact(out);
}

/** Lowest unused `w<N>` id for this board. */
export function nextWidgetId(list) {
  let n = 1;
  while (list.some((widget) => widget.i === `w${n}`)) n += 1;
  return `w${n}`;
}

/**
 * First cell (scanning row by row, left to right) where a `w`x`h` widget fits
 * without overlapping anything. Falls back to the origin — compaction will
 * sort out the collision.
 */
export function findFreeSpot(list, w, h, cols) {
  const width = Math.min(w, cols);
  for (let y = 0; y < MAX_SCAN_ROWS; y += 1) {
    for (let x = 0; x <= cols - width; x += 1) {
      const candidate = { i: '__new', x, y, w: width, h };
      if (!list.some((other) => overlaps(other, candidate))) return { x, y };
    }
  }
  return { x: 0, y: 0 };
}

/**
 * Fit every widget inside `cols`, then compact. This is the one place a board
 * narrows — a widget wider than the board is shrunk and pulled back to the
 * left edge instead of being dropped. The desktop and phone boards are edited
 * separately, so this only runs when a stored board disagrees with the column
 * count it claims (a repaired file, or a board seeded at another width).
 */
export function clampToCols(list, cols) {
  return compact(
    list.map((widget) => {
      const w = Math.max(1, Math.min(widget.w, cols));
      return { ...widget, w, x: Math.max(0, Math.min(widget.x, cols - w)) };
    }),
  );
}
