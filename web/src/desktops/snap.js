// Putting a window into half the screen, as arithmetic.
//
// Like `window-state.js` this is rectangles and nothing else, because the part
// that has to keep being right is the part that decides what a drag near an
// edge *means* — and that answer has to be checkable without a browser, a
// pointer or a server.
//
// Two rules the shapes here exist to enforce.
//
// **A split never invents a second copy of a window.** Dropping a view into the
// left half when it is already the right half is a move, not a clone. The other
// half either keeps the view it had or becomes an explicitly empty slot with a
// picker in it — never a duplicate of the one that just moved.
//
// **An empty slot is a state, not an absence.** A pane whose view was closed or
// minimized stays a labelled empty pane until somebody fills it or leaves split
// mode, so nothing silently rearranges itself around the gap.

/** How close to an edge a pointer must be for a snap to be offered. */
export const SNAP_EDGE = 48;

/** The divider's visual width. Its hit region is deliberately larger. */
export const GUTTER = 8;

/** How far the divider may move, as the primary pane's share. */
export const MIN_RATIO = 0.2;
export const MAX_RATIO = 0.8;

/** One keyboard press of the divider. */
export const RATIO_STEP = 0.02;

/**
 * Which half a pointer is offering to snap into, or null.
 *
 * Only ever left or right. A top edge means maximize in most desktops and it
 * would need its own preview, its own undo and its own keyboard equivalent, so
 * it is deliberately not half-built here.
 */
export function snapTargetFor(point, area) {
  if (!area?.width || !Number.isFinite(point?.x)) return null;
  if (point.y < 0 || point.y > area.height) return null;
  if (point.x <= SNAP_EDGE) return 'left';
  if (point.x >= area.width - SNAP_EDGE) return 'right';
  return null;
}

/** The rectangle a snap preview should draw, in work-area coordinates. */
export function previewBounds(side, area, ratio = 0.5) {
  const usable = Math.max(0, area.width - GUTTER);
  const primary = Math.round(usable * ratio);
  if (side === 'left') return { x: 0, y: 0, width: primary, height: area.height };
  return { x: primary + GUTTER, y: 0, width: area.width - primary - GUTTER, height: area.height };
}

/**
 * The layout after putting one view into one half.
 *
 * Returns the patch to save, or null when nothing would change. The other pane
 * keeps whatever it had unless that was this same view, in which case it
 * becomes empty — because a window cannot be both halves and pretending
 * otherwise is how a second copy appears.
 */
export function snapPatch(layout, viewId, side) {
  const wanted = side === 'left' ? 'primaryView' : 'secondaryView';
  const other = side === 'left' ? 'secondaryView' : 'primaryView';
  if (layout.arrangement === 'split' && layout[wanted] === viewId) return null;
  const keep = layout.arrangement === 'split' && layout[other] !== viewId ? layout[other] : null;
  return {
    arrangement: 'split',
    [wanted]: viewId,
    [other]: keep,
    dividerRatio: layout.dividerRatio || 0.5,
    selectedView: viewId,
  };
}

/** The layout after exchanging the two panes. Neither view is touched. */
export function swapPatch(layout) {
  if (layout.arrangement !== 'split') return null;
  return {
    arrangement: 'split',
    primaryView: layout.secondaryView || null,
    secondaryView: layout.primaryView || null,
    // The ratio belongs to the divider's position on screen, so swapping the
    // views mirrors it: the pane a person made wider stays the wider pane.
    dividerRatio: Number((1 - (layout.dividerRatio || 0.5)).toFixed(4)),
  };
}

/** The layout after leaving split mode. Both views go back to floating. */
export function exitPatch() {
  return { arrangement: 'floating', primaryView: null, secondaryView: null };
}

/**
 * The layout after a view leaves a pane.
 *
 * Closing or minimizing one member does not collapse the split. The slot stays
 * there and says it is empty, which is what makes "restore it back into where
 * it was" possible at all.
 */
export function vacatePatch(layout, viewId) {
  if (layout.arrangement !== 'split') return null;
  if (layout.primaryView === viewId) return { primaryView: null };
  if (layout.secondaryView === viewId) return { secondaryView: null };
  return null;
}

/** Whether a minimized split member can go straight back to its own slot. */
export function freeSlotFor(layout, viewId) {
  if (layout.arrangement !== 'split') return null;
  if (layout.primaryView === viewId || layout.secondaryView === viewId) return null;
  if (!layout.primaryView) return 'left';
  if (!layout.secondaryView) return 'right';
  return null;
}

/** The panes a split has, each with its side, its view id and whether it is empty. */
export function panes(layout) {
  if (layout.arrangement !== 'split') return [];
  return [
    { side: 'left', key: 'primaryView', viewId: layout.primaryView || null },
    { side: 'right', key: 'secondaryView', viewId: layout.secondaryView || null },
  ];
}

/** A ratio nudged by one keyboard step, kept inside the usable range. */
export function stepRatio(ratio, direction, step = RATIO_STEP) {
  const next = (Number.isFinite(ratio) ? ratio : 0.5) + direction * step;
  return Number(Math.min(MAX_RATIO, Math.max(MIN_RATIO, next)).toFixed(4));
}

/** The ratio a pointer at `x` implies, kept off both ends. */
export function ratioAt(x, area) {
  if (!area?.width) return 0.5;
  return Number(
    Math.min(MAX_RATIO, Math.max(MIN_RATIO, Math.round((x / area.width) * 1000) / 1000)).toFixed(4),
  );
}
