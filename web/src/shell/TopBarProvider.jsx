// The status items apps have published, held for as long as their window is.
//
// These are runtime state and nothing else. They are not stored on the server,
// they do not survive a restart, and an app republishes them from `Vela.ready`
// — the same bargain widget summaries make, except that a widget belongs to an
// app and one of these belongs to an open window. Decision D05 in
// `plans/TOP-BAR-PROGRESS.md` records why they live here rather than in
// `app-data.sqlite`: an app session names the app and the installation, never
// the view, and items that must not outlive a window have no business in a
// database whose whole job is outliving things.
//
// The bridge writes; the top bar reads. Both are under this provider because
// one is inside a window on the desk and the other is the shell around it.
import { createContext, useCallback, useContext, useMemo, useState } from 'react';

const TopBarContext = createContext(null);

/** Nothing published, and nowhere to publish to. Used outside the provider. */
const OUTSIDE = {
  items: [],
  itemsFor: () => [],
  publish: () => {},
  clear: () => {},
};

export default function TopBarProvider({ children }) {
  // `{ [viewId]: { appId, items } }`. Keyed by view so two windows of the same
  // app keep their own items, and so closing one takes only its own down.
  const [published, setPublished] = useState({});

  const publish = useCallback((viewId, appId, items) => {
    if (!viewId) return;
    setPublished((previous) => {
      // An empty list is how an app takes its items down, so it removes the
      // entry rather than storing an empty one that the bar would step over.
      if (!items?.length) {
        if (!previous[viewId]) return previous;
        const next = { ...previous };
        delete next[viewId];
        return next;
      }
      return { ...previous, [viewId]: { appId, items } };
    });
  }, []);

  /** The window closed, or its bridge went away. Either way, so do its items. */
  const clear = useCallback((viewId) => {
    if (!viewId) return;
    setPublished((previous) => {
      if (!previous[viewId]) return previous;
      const next = { ...previous };
      delete next[viewId];
      return next;
    });
  }, []);

  const value = useMemo(() => {
    const items = Object.entries(published).flatMap(([viewId, entry]) =>
      entry.items.map((item) => ({ ...item, viewId, appId: entry.appId })),
    );
    return {
      items,
      itemsFor: (viewId) => published[viewId]?.items || [],
      publish,
      clear,
    };
  }, [published, publish, clear]);

  return <TopBarContext.Provider value={value}>{children}</TopBarContext.Provider>;
}

export function useTopBarItems() {
  return useContext(TopBarContext) || OUTSIDE;
}
