// What a window shortcut means, as data.
//
// Like `window-state.js` and `snap.js` this is decisions and nothing else, so
// the answers — which window a switcher starts on, what "show the desktop"
// remembers, what Alt+↓ does to a maximized window — can be checked without a
// browser, a keyboard or a server.

/**
 * Every window on the desktop, the one in front first. Minimized ones are included.
 *
 * The selected window leads whatever the stack says. The stack comes back from
 * the server a moment after a raise, and the selection is what the person
 * just did, so a switcher opened in that moment still starts from where they
 * are.
 */
export function frontToBack(ordered, selectedId = null) {
  const list = (ordered || []).slice().reverse();
  const at = selectedId ? list.findIndex((view) => view.id === selectedId) : -1;
  if (at > 0 && !list[at].window?.minimized) list.unshift(...list.splice(at, 1));
  return list;
}

/**
 * The switcher as it opens.
 *
 * It starts on the window behind the front one, so one tap goes back to the
 * window you were in before. With only one window there is nowhere else to go,
 * and it starts on that one.
 */
export function openSwitcher(ordered, direction = 1, selectedId = null) {
  const ids = frontToBack(ordered, selectedId).map((view) => view.id);
  if (!ids.length) return null;
  const index = ids.length === 1 ? 0 : direction > 0 ? 1 : ids.length - 1;
  return { ids, index };
}

/** One more press of the key, forwards or backwards, wrapping at the ends. */
export function stepSwitcher(switcher, direction = 1) {
  if (!switcher?.ids?.length) return switcher;
  const count = switcher.ids.length;
  return { ...switcher, index: (switcher.index + direction + count) % count };
}

/** The window the switcher has chosen. */
export function chosenWindow(switcher) {
  return switcher?.ids?.[switcher.index] ?? null;
}

/**
 * What Alt+↑ and Alt+↓ do to the window in front.
 *
 * Up maximizes, and does nothing to a window already maximized or one whose
 * app said it cannot be. Down steps back the way up came: a maximized window is
 * restored, and anything else is minimized.
 */
export function verticalCommand(layout, viewId, direction, { maximizable = true } = {}) {
  const maximized = layout?.arrangement === 'maximized' && layout.maximizedView === viewId;
  if (direction > 0) return maximized || !maximizable ? null : 'maximize';
  return maximized ? 'restore' : 'minimize';
}

const REMEMBERED = ['arrangement', 'maximizedView', 'primaryView', 'secondaryView', 'dividerRatio'];

/**
 * What "show the desktop" puts away, and what it will need to bring back.
 *
 * The windows that are on screen, back to front, and the arrangement they were
 * in — so a split comes back as the same split, and a maximized window comes
 * back maximized. Returns null when nothing is on screen, which is the moment
 * the same key means "bring them back" instead.
 */
export function showDesktopSnapshot(layout, ordered) {
  const shown = (ordered || []).filter((view) => !view.window?.minimized).map((view) => view.id);
  if (!shown.length) return null;
  const remembered = {};
  for (const key of REMEMBERED) remembered[key] = layout?.[key] ?? null;
  return { viewIds: shown, layout: remembered };
}

/**
 * Bringing back what "show the desktop" put away.
 *
 * Only windows that are still open and still minimized come back — one closed
 * or brought back by hand in the meantime is not touched — and the arrangement
 * forgets any pane whose window is gone, rather than reaching for a view that
 * no longer exists. Returns `{ viewIds, layout }` in the order to raise them,
 * back to front, or null when there is nothing left to restore.
 */
export function restoreFromSnapshot(snapshot, ordered) {
  if (!snapshot?.viewIds?.length) return null;
  const minimized = new Set(
    (ordered || []).filter((view) => view.window?.minimized).map((view) => view.id),
  );
  const viewIds = snapshot.viewIds.filter((id) => minimized.has(id));
  if (!viewIds.length) return null;
  const open = new Set((ordered || []).map((view) => view.id));
  const keep = (id) => (id && open.has(id) ? id : null);
  const layout = { ...snapshot.layout };
  layout.primaryView = keep(layout.primaryView);
  layout.secondaryView = keep(layout.secondaryView);
  layout.maximizedView = keep(layout.maximizedView);
  if (layout.arrangement === 'maximized' && !layout.maximizedView) {
    layout.arrangement = 'floating';
  }
  if (layout.arrangement === 'split' && !layout.primaryView && !layout.secondaryView) {
    layout.arrangement = 'floating';
  }
  return { viewIds, layout };
}

/**
 * The layout patch that puts every pane's window away at once.
 *
 * Minimizing a split member leaves its pane standing empty. Doing that one
 * window at a time would be two saves against one revision, and the second
 * would lose; this is the same result as one save.
 */
export function vacateAllPatch(layout, viewIds) {
  if (layout?.arrangement !== 'split') return null;
  const away = new Set(viewIds);
  const patch = {};
  if (away.has(layout.primaryView)) patch.primaryView = null;
  if (away.has(layout.secondaryView)) patch.secondaryView = null;
  return Object.keys(patch).length ? { arrangement: 'split', ...patch } : null;
}
