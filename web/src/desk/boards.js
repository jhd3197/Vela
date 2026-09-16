// The desk's board model.
//
// Two boards, never one. `desktop` is six columns, `phone` is two, and they
// are edited and stored separately: a widget arrangement that works on a
// 1440px desk is not the same arrangement that works in a hand, and reflowing
// one into the other silently rearranges a layout the user chose.
import { clampToCols, DESKTOP_COLS, PHONE_COLS, overlaps } from './grid/layout.js';

export const BOARD_VERSION = 1;
export const BOARD_KEYS = ['desktop', 'phone'];
export const BOARD_COLS = { desktop: DESKTOP_COLS, phone: PHONE_COLS };
/** Mirrors `MAX_WIDGETS_PER_BOARD` in `vela/desk.py`. */
export const MAX_WIDGETS_PER_BOARD = 40;

const widget = (i, type, x, y, w, h, cfg = {}) => ({ i, type, x, y, w, h, cfg });

/**
 * What a desk looks like before anyone arranges it: the apps the user has,
 * the time, what is running, and a way in to Ask. Every seeded widget draws
 * data Vela already has.
 */
export function defaultBoards() {
  return {
    version: BOARD_VERSION,
    desktop: {
      cols: DESKTOP_COLS,
      // A board that fills the width with no gaps: the time and what is running
      // down the left, the app grid and Ask filling the rest, and the things
      // that need you filling the bottom-left. Only widgets that always have
      // something real to show are seeded — volumes and the like are opt-in.
      widgets: [
        widget('w1', 'clock', 0, 0, 2, 1),
        widget('w2', 'apps', 2, 0, 4, 2),
        widget('w3', 'running', 0, 1, 2, 1),
        widget('w4', 'needs-you', 0, 2, 2, 2),
        widget('w5', 'ask', 2, 2, 4, 2),
      ],
    },
    phone: {
      cols: PHONE_COLS,
      widgets: [
        widget('w1', 'clock', 0, 0, 2, 1),
        widget('w2', 'needs-you', 0, 1, 2, 1),
        widget('w3', 'apps', 0, 2, 2, 3),
        widget('w4', 'ask', 0, 5, 2, 1),
      ],
    },
  };
}

/** The column count a board claims, falling back to the one its key implies. */
export function colsOf(boards, key) {
  const cols = Number(boards?.[key]?.cols);
  return Number.isInteger(cols) && cols > 0 ? cols : BOARD_COLS[key];
}

/** The widget list for one board, always an array. */
export function widgetsOf(boards, key) {
  const list = boards?.[key]?.widgets;
  return Array.isArray(list) ? list : [];
}

/** Replace one board's widgets, leaving the other board untouched. */
export function withWidgets(boards, key, widgets) {
  return {
    ...boards,
    version: BOARD_VERSION,
    [key]: { ...(boards?.[key] || {}), cols: colsOf(boards, key), widgets },
  };
}

const isGeometry = (value) => Number.isInteger(value) && value >= 0;

/**
 * Make a board renderable without throwing anything away that can be saved.
 *
 * The server validates and repairs too (`vela/desk.py`); this runs on what the
 * browser holds, so a board edited in another tab, or one naming a widget type
 * that is no longer installed, still draws. Unknown types are dropped rather
 * than rendered as an error the user cannot act on.
 */
export function repairBoard(widgets, cols, knownTypes) {
  const known = knownTypes ? new Set(knownTypes) : null;
  const seen = new Set();
  const kept = [];
  for (const entry of Array.isArray(widgets) ? widgets : []) {
    if (!entry || typeof entry !== 'object') continue;
    const { i, type } = entry;
    if (typeof i !== 'string' || !i || seen.has(i)) continue;
    if (typeof type !== 'string' || !type) continue;
    if (known && !known.has(type)) continue;
    if (!isGeometry(entry.x) || !isGeometry(entry.y)) continue;
    if (!Number.isInteger(entry.w) || entry.w < 1) continue;
    if (!Number.isInteger(entry.h) || entry.h < 1) continue;
    seen.add(i);
    kept.push({
      i,
      type,
      x: entry.x,
      y: entry.y,
      w: entry.w,
      h: entry.h,
      cfg: entry.cfg && typeof entry.cfg === 'object' ? entry.cfg : {},
    });
    if (kept.length >= MAX_WIDGETS_PER_BOARD) break;
  }
  const fitted = clampToCols(kept, cols);
  // Compaction resolves the overlaps a hand-edited file can contain; this is
  // the belt to that braces, and costs nothing on a 40-widget board.
  const placed = [];
  for (const entry of fitted) {
    let candidate = entry;
    while (placed.some((other) => overlaps(other, candidate))) {
      candidate = { ...candidate, y: candidate.y + 1 };
    }
    placed.push(candidate);
  }
  return placed;
}

/** Repair both boards of a payload from the server or from another tab. */
export function repairBoards(boards, knownTypes) {
  const out = { version: BOARD_VERSION };
  for (const key of BOARD_KEYS) {
    const cols = colsOf(boards, key);
    out[key] = { cols, widgets: repairBoard(widgetsOf(boards, key), cols, knownTypes) };
  }
  return out;
}
