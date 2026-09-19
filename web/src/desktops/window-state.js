// Where a window goes, as arithmetic.
//
// No React, no DOM, no fetch — just rectangles — because this is the part that
// has to keep being right after someone rotates a tablet, zooms to 200%, drags
// a browser window to a smaller screen, or restores a layout saved on a monitor
// they no longer own. Geometry that arrives from any of those is checked here
// rather than trusted, and it is checked somewhere a test can reach without a
// browser.
//
// Everything is in CSS pixels, relative to the work area — the space beside the
// rail and above the status strip — so the stored numbers do not change meaning
// when the surrounding chrome does.

/** The smallest a window may be, and the smallest part of it that must stay reachable. */
export const MIN_WIDTH = 240;
export const MIN_HEIGHT = 160;

/** A title bar dragged off the top is a window nobody can get back. */
export const TITLE_BAR = 38;

/** How far a window must remain inside the work area horizontally. */
const KEEP_VISIBLE = 64;

/** Each new window steps down and right from the last, up to a point. */
const CASCADE_STEP = 28;
const CASCADE_WRAP = 6;

/** A new window takes this much of the work area, within its own limits. */
const DEFAULT_FRACTION = 0.68;

/**
 * The narrowest work area two panes are worth showing in.
 *
 * Below it a split is kept — the arrangement somebody made is still their
 * arrangement, and rotating a tablet back restores it exactly — but only one
 * pane is *drawn*, full width, with the rail switching between them. Two
 * 200-pixel columns are not a split screen; they are two windows nobody can
 * read.
 */
export const SPLIT_MIN_WIDTH = 720;

const round = (value) => Math.round(value);

/** A declared `{ width, height }` with usable numbers in it. */
export function isSize(value) {
  return Boolean(
    value &&
    ['width', 'height'].every(
      (key) => typeof value[key] === 'number' && Number.isFinite(value[key]) && value[key] > 0,
    ),
  );
}

/** A rectangle with numbers in it, or null. */
export function isBounds(value) {
  return Boolean(
    value &&
    ['x', 'y', 'width', 'height'].every(
      (key) => typeof value[key] === 'number' && Number.isFinite(value[key]),
    ),
  );
}

/**
 * The area windows live in, from the element that holds them.
 *
 * Measured rather than assumed: the rail's width, the status strip's height and
 * the safe-area insets all move, and a layout that hardcoded them would drift
 * every time one did.
 */
export function workArea(rect) {
  if (!rect || !Number.isFinite(rect.width) || !Number.isFinite(rect.height)) {
    return { width: 0, height: 0 };
  }
  return { width: Math.max(0, round(rect.width)), height: Math.max(0, round(rect.height)) };
}

/**
 * Put a window back inside the work area without moving it more than necessary.
 *
 * Size is reduced only when the area genuinely cannot hold it, and position is
 * nudged only far enough that some of the title bar is reachable. A restored
 * layout from a bigger screen should still look like the arrangement someone
 * made, not like everything piled in the corner.
 */
export function clampBounds(bounds, area) {
  if (!isBounds(bounds)) return null;
  const width = Math.max(MIN_WIDTH, Math.min(round(bounds.width), Math.max(MIN_WIDTH, area.width)));
  const height = Math.max(
    MIN_HEIGHT,
    Math.min(round(bounds.height), Math.max(MIN_HEIGHT, area.height)),
  );
  // Horizontally a window may hang off either edge, as long as enough of it is
  // still there to grab. Vertically the title bar cannot go above the top at
  // all, because there is nothing above it to grab it by.
  const minX = Math.min(0, area.width - width);
  const x = Math.min(
    Math.max(round(bounds.x), -(width - KEEP_VISIBLE)),
    Math.max(minX, area.width - KEEP_VISIBLE),
  );
  const y = Math.min(Math.max(round(bounds.y), 0), Math.max(0, area.height - TITLE_BAR));
  return { x, y, width, height };
}

/**
 * Where a window with no saved place should open.
 *
 * `index` is how many windows are already open, so the second one does not land
 * exactly on the first. It wraps rather than marching off the screen.
 *
 * `size` is the app's declared `view.window.defaultSize`, when it has one. It
 * replaces the fraction of the work area a window would otherwise take, which
 * is a sensible guess for a document and a bad one for a calculator. It is
 * still clamped: a size that does not fit the screen in front of somebody is
 * not a size, whoever declared it.
 */
export function defaultBounds(area, index = 0, size = null) {
  const wanted = isSize(size) ? size : null;
  const width = Math.max(
    MIN_WIDTH,
    Math.min(round(wanted ? wanted.width : area.width * DEFAULT_FRACTION), area.width),
  );
  const height = Math.max(
    MIN_HEIGHT,
    Math.min(round(wanted ? wanted.height : area.height * DEFAULT_FRACTION), area.height),
  );
  const step = (index % CASCADE_WRAP) * CASCADE_STEP;
  return clampBounds(
    {
      x: round((area.width - width) / 2) + step,
      y: round((area.height - height) / 2) + step,
      width,
      height,
    },
    area,
  );
}

/** A maximized window fills the work area exactly. */
export function maximizedBounds(area) {
  return {
    x: 0,
    y: 0,
    width: Math.max(MIN_WIDTH, area.width),
    height: Math.max(MIN_HEIGHT, area.height),
  };
}

/**
 * One half of a two-pane split, with the divider's gutter taken out.
 *
 * `ratio` is the primary pane's share. The gutter is removed from the panes
 * rather than drawn over them, so the divider never covers app content.
 */
export function splitBounds(area, ratio, side, gutter = 8) {
  const usable = Math.max(MIN_WIDTH * 2, area.width - gutter);
  const primary = Math.max(MIN_WIDTH, Math.min(round(usable * ratio), usable - MIN_WIDTH));
  if (side === 'primary') {
    return { x: 0, y: 0, width: primary, height: Math.max(MIN_HEIGHT, area.height) };
  }
  return {
    x: primary + gutter,
    y: 0,
    width: Math.max(MIN_WIDTH, area.width - primary - gutter),
    height: Math.max(MIN_HEIGHT, area.height),
  };
}

/**
 * Where a window should be drawn right now, given the layout it is in.
 *
 * The one place that answers "what does this window look like", so the frame
 * component does not have to know about arrangements and the arrangement does
 * not have to know about pixels. A minimized window returns null: it is not
 * drawn at all, which is different from being drawn somewhere off-screen.
 */
export function placeView(view, { layout, area, index = 0, gutter = 8, size = null }) {
  if (view.window?.minimized) return null;
  if (layout.arrangement === 'maximized') {
    return layout.maximizedView === view.id ? maximizedBounds(area) : null;
  }
  if (layout.arrangement === 'split') {
    const member =
      layout.primaryView === view.id
        ? 'primary'
        : layout.secondaryView === view.id
          ? 'secondary'
          : null;
    if (!member) return null;
    if (area.width < SPLIT_MIN_WIDTH) {
      // One at a time on a narrow screen, and the stored split is untouched.
      // The one in front is whichever is selected, falling back to the pane
      // that has something in it.
      const chosen =
        layout.selectedView &&
        (layout.selectedView === layout.primaryView || layout.selectedView === layout.secondaryView)
          ? layout.selectedView
          : null;
      const front = chosen || layout.primaryView || layout.secondaryView;
      return view.id === front ? maximizedBounds(area) : null;
    }
    return splitBounds(area, layout.dividerRatio, member, gutter);
  }
  // A place this window has been put beats the app's own suggestion: a
  // declared size seeds a window, it does not re-seed it every time the desk
  // is drawn. Somebody who moved or resized it meant to.
  const saved = clampBounds(view.window?.bounds, area);
  return saved || defaultBounds(area, index, size);
}

/**
 * What to send when a window is minimized.
 *
 * The bounds it had are kept as `restoreBounds` so it comes back where it was
 * rather than where a fresh window would go. Its current bounds are left alone:
 * minimizing is not moving.
 */
export function minimizePatch(view, area) {
  const current = clampBounds(view.window?.bounds, area);
  return { minimized: true, ...(current ? { restoreBounds: current } : {}) };
}

/**
 * What to send when a window is restored.
 *
 * Its own remembered place if it has one and it still fits, and a default
 * placement otherwise — which is what happens when the saved place came from a
 * screen that is no longer attached.
 */
export function restorePatch(view, area, index = 0, size = null) {
  const remembered = view.window?.restoreBounds || view.window?.bounds;
  const bounds = clampBounds(remembered, area) || defaultBounds(area, index, size);
  return { minimized: false, bounds, raise: true };
}

/**
 * What to send when a window is maximized, or null when it must not be.
 *
 * Maximizing is a layout change, so the answer is a layout patch rather than a
 * patch to the window. `maximizable` is the app's declared option, and a
 * refusal is null rather than a thrown error: a control that should not have
 * been offered doing nothing is the right outcome, and the window's own chrome
 * does not offer it in the first place.
 *
 * Restoring is never refused. A window that somehow ended up maximized — a
 * layout saved before the app declared itself fixed-size, an arrangement
 * restored from another machine — has to be able to come back, and refusing
 * that would leave it stuck at full screen with the control hidden.
 */
export function maximizePatch(layout, viewId, { maximizable = true } = {}) {
  const isMaximized = layout.arrangement === 'maximized' && layout.maximizedView === viewId;
  if (isMaximized) return { arrangement: 'floating', maximizedView: null };
  if (!maximizable) return null;
  return { arrangement: 'maximized', maximizedView: viewId };
}

/**
 * Bounds after a drag or a resize, in the work area's coordinates.
 *
 * `delta` is how far the pointer moved. Resizing pulls the edge the user
 * grabbed; dragging moves the whole window. Either way the result is clamped,
 * so a gesture cannot put a window somewhere it cannot be got back from.
 */
export function moveBounds(bounds, delta, area) {
  return clampBounds({ ...bounds, x: bounds.x + delta.x, y: bounds.y + delta.y }, area);
}

export function resizeBounds(bounds, delta, edge, area) {
  const next = { ...bounds };
  if (edge.includes('e')) next.width = bounds.width + delta.x;
  if (edge.includes('s')) next.height = bounds.height + delta.y;
  if (edge.includes('w')) {
    // Pulling the left edge past the minimum moves the edge, not the window.
    const width = Math.max(MIN_WIDTH, bounds.width - delta.x);
    next.x = bounds.x + (bounds.width - width);
    next.width = width;
  }
  if (edge.includes('n')) {
    const height = Math.max(MIN_HEIGHT, bounds.height - delta.y);
    next.y = bounds.y + (bounds.height - height);
    next.height = height;
  }
  return clampBounds(next, area);
}

/** The divider ratio a pointer at `x` implies, kept off both ends. */
export function dividerRatio(x, area, min = 0.2, max = 0.8) {
  if (!area.width) return 0.5;
  const raw = x / area.width;
  return Math.min(max, Math.max(min, Math.round(raw * 1000) / 1000));
}

/** Views in the order they should be painted: back to front. */
export function stackOrder(views) {
  return views.slice().sort((a, b) => (a.window?.stack || 0) - (b.window?.stack || 0));
}

/**
 * Each view with the depth its stack gives it, in an order that never changes.
 *
 * Which window is in front is state — `window.stack`, kept by the server — and
 * a window says so with a layer, not with its place in the document. Drawing
 * them in stack order instead meant that raising one *moved its element*, and
 * moving an element that contains an iframe reloads that iframe: clicking a
 * window to bring it forward restarted the app inside it, threw away whatever
 * had been typed into it, and took down anything it had published. So the
 * order here is by id — arbitrary, and above all stable — and `depth` is what
 * carries the arrangement.
 *
 * `depth` is also how far along the cascade a window with no saved place sits,
 * which is why it is one number rather than two.
 */
export function stackLayers(views) {
  const depths = new Map(stackOrder(views).map((view, index) => [view.id, index]));
  return views
    .slice()
    .sort((a, b) => String(a.id).localeCompare(String(b.id)))
    .map((view) => ({ view, depth: depths.get(view.id) ?? 0 }));
}
