// What is open on the selected desktop, and the things you can do to it.
//
// The server is the record; this is a copy of it that reacts quickly. Moving a
// window updates the copy first and writes afterwards, because a window that
// waited for a round trip before following the pointer is a window that feels
// broken. Anything that can fail meaningfully — opening, closing, saving a
// layout — reports its failure rather than pretending.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { desktopsApi } from './desktopsApi.js';
import { exitPatch, freeSlotFor, snapPatch, swapPatch, vacatePatch } from './snap.js';
import { minimizePatch, restorePatch, stackOrder } from './window-state.js';

const EMPTY = {
  views: [],
  layout: {
    revision: 0,
    arrangement: 'floating',
    maximizedView: null,
    primaryView: null,
    secondaryView: null,
    dividerRatio: 0.5,
    selectedView: null,
  },
};

export default function useDesktopViews(desktopId) {
  const [state, setState] = useState({ ...EMPTY, loaded: false, error: null });
  // A move writes on a trailing edge: dragging a window emits a patch per
  // frame, and every one of those becoming a request would be a request storm
  // for one gesture.
  const pending = useRef(new Map());
  const timers = useRef(new Map());

  const load = useCallback(async () => {
    if (!desktopId) {
      setState({ ...EMPTY, loaded: false, error: null });
      return null;
    }
    try {
      const payload = await desktopsApi.views(desktopId);
      // A response that is not the shape this expects is a bug somewhere, and
      // the right answer to it is an empty desktop rather than a dashboard that
      // will not render at all.
      const next = {
        views: Array.isArray(payload?.views) ? payload.views : [],
        layout: payload?.layout?.arrangement ? payload.layout : EMPTY.layout,
      };
      setState({ ...next, loaded: true, error: null });
      return next;
    } catch (error) {
      setState((previous) => ({ ...previous, loaded: true, error }));
      return null;
    }
  }, [desktopId]);

  useEffect(() => {
    setState({ ...EMPTY, loaded: false, error: null });
    load();
  }, [load]);

  // Any timer still holding a window's position has to fire before this hook
  // goes away, or the last thing the user did to it is lost.
  const flush = useCallback(async () => {
    const entries = [...pending.current.entries()];
    pending.current.clear();
    for (const timer of timers.current.values()) clearTimeout(timer);
    timers.current.clear();
    await Promise.all(
      entries.map(([viewId, patch]) =>
        desktopsApi.updateView(desktopId, viewId, patch).catch(() => null),
      ),
    );
  }, [desktopId]);

  useEffect(() => () => void flush(), [flush]);

  /** Change a window locally now, and tell the server shortly. */
  const patchView = useCallback(
    (viewId, patch, { immediate = false } = {}) => {
      setState((previous) => ({
        ...previous,
        views: previous.views.map((view) =>
          view.id === viewId ? { ...view, window: { ...view.window, ...patch } } : view,
        ),
      }));
      const merged = { ...(pending.current.get(viewId) || {}), ...patch };
      pending.current.set(viewId, merged);
      clearTimeout(timers.current.get(viewId));
      const send = async () => {
        const body = pending.current.get(viewId);
        pending.current.delete(viewId);
        timers.current.delete(viewId);
        if (!body) return;
        const saved = await desktopsApi.updateView(desktopId, viewId, body).catch(() => null);
        // The server owns the stacking order, so a raise has to come back
        // rather than being guessed at locally.
        if (saved) {
          setState((previous) => ({
            ...previous,
            views: previous.views.map((view) => (view.id === saved.id ? saved : view)),
          }));
        }
      };
      if (immediate) send();
      else timers.current.set(viewId, setTimeout(send, 200));
    },
    [desktopId],
  );

  const open = useCallback(
    async (request) => {
      const view = await desktopsApi.openView(desktopId, request);
      await load();
      return view;
    },
    [desktopId, load],
  );

  const select = useCallback(
    async (viewId) => {
      setState((previous) => ({
        ...previous,
        layout: { ...previous.layout, selectedView: viewId },
      }));
      const layout = await desktopsApi.selectView(desktopId, viewId).catch(() => null);
      if (layout) setState((previous) => ({ ...previous, layout }));
    },
    [desktopId],
  );

  const saveLayout = useCallback(
    async (patch) => {
      try {
        const layout = await desktopsApi.saveLayout(desktopId, state.layout.revision, patch);
        setState((previous) => ({ ...previous, layout }));
        return { ok: true, layout };
      } catch (error) {
        if (error.status === 409) {
          // Somebody else arranged this desktop. Their arrangement is the one
          // that is really stored, so it replaces the draft rather than being
          // overwritten by it.
          const fresh = await load();
          return { ok: false, conflict: true, layout: fresh?.layout || null };
        }
        return { ok: false, message: error.message || 'Could not save the layout.' };
      }
    },
    [desktopId, state.layout.revision, load],
  );

  const close = useCallback(
    async (viewId) => {
      const vacated = vacatePatch(state.layout, viewId);
      await desktopsApi.closeView(desktopId, viewId);
      // Said before the reload so the empty slot is what comes back, rather
      // than a split that briefly looks like it lost a pane.
      if (vacated) await saveLayout({ arrangement: 'split', ...vacated }).catch(() => null);
      await load();
    },
    [desktopId, load, saveLayout, state.layout],
  );

  const area = useRef({ width: 0, height: 0 });
  const setArea = useCallback((next) => {
    area.current = next;
  }, []);

  const minimize = useCallback(
    (view) => {
      // A pane whose member went away stays a pane. Collapsing the split here
      // would take the slot the person expects to restore into.
      const vacated = vacatePatch(state.layout, view.id);
      if (vacated) saveLayout({ arrangement: 'split', ...vacated });
      return patchView(view.id, minimizePatch(view, area.current), { immediate: true });
    },
    [patchView, saveLayout, state.layout],
  );

  const restore = useCallback(
    (view, index) => {
      // Back into its own slot if that slot is still free, and floating
      // otherwise — never evicting whatever took its place in the meantime.
      const slot = freeSlotFor(state.layout, view.id);
      if (slot) {
        patchView(view.id, { minimized: false, raise: true }, { immediate: true });
        return saveLayout(snapPatch(state.layout, view.id, slot));
      }
      return patchView(view.id, restorePatch(view, area.current, index), { immediate: true });
    },
    [patchView, saveLayout, state.layout],
  );

  // ---- split placement
  //
  // Every one of these is a layout change and nothing else. No view is closed,
  // reopened, serialized or recreated to move between panes: a window keeps its
  // session wherever it is drawn, which is the whole reason the arrangement and
  // the view are separate records.

  const snap = useCallback(
    (viewId, side) => {
      const patch = snapPatch(state.layout, viewId, side);
      if (!patch) return Promise.resolve({ ok: true });
      // A window cannot be both in a pane and minimized. Snapping is an
      // explicit request to see it, so it comes back if it was away.
      patchView(viewId, { minimized: false, raise: true }, { immediate: true });
      return saveLayout(patch);
    },
    [patchView, saveLayout, state.layout],
  );

  const swapPanes = useCallback(() => {
    const patch = swapPatch(state.layout);
    return patch ? saveLayout(patch) : Promise.resolve({ ok: true });
  }, [saveLayout, state.layout]);

  const exitSplit = useCallback(() => saveLayout(exitPatch()), [saveLayout]);

  const setDivider = useCallback(
    (ratio) => saveLayout({ arrangement: 'split', dividerRatio: ratio }),
    [saveLayout],
  );

  const maximize = useCallback(
    (view) =>
      saveLayout(
        state.layout.arrangement === 'maximized' && state.layout.maximizedView === view.id
          ? { arrangement: 'floating', maximizedView: null }
          : { arrangement: 'maximized', maximizedView: view.id },
      ),
    [saveLayout, state.layout],
  );

  const ordered = useMemo(() => stackOrder(state.views), [state.views]);

  return {
    ...state,
    // Which workspace all of this belongs to. Carried so a consumer never has
    // to be told separately and get it wrong.
    desktopId,
    ordered,
    refresh: load,
    open,
    close,
    select,
    patchView,
    minimize,
    restore,
    maximize,
    snap,
    swapPanes,
    exitSplit,
    setDivider,
    saveLayout,
    setArea,
    flush,
  };
}
