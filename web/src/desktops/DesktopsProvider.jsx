// Which desktops exist, which one this browser is looking at, and how it is dressed.
//
// The selection is deliberately local to the device. A phone and a laptop are
// two people looking at the same server as far as Vela is concerned, and one of
// them choosing Desktop 2 must not make the other jump. That is also why the
// selection is validated on every load: a desktop deleted on the laptop leaves
// the phone pointing at an id that is gone, and the honest answer is to fall
// back to the first one rather than show an empty workspace.
//
// Appearance lives here too, because the Desk and the Launchpad both draw over
// the same wallpaper and reading it twice would let them disagree for a moment.
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { desktopsApi } from './desktopsApi.js';
import useDesktopViews from './useDesktopViews.js';
import { DEFAULT_WALLPAPER } from '../desk/wallpaper.js';

const DesktopsContext = createContext(null);

const STORAGE_KEY = 'vela:selected-desktop';

const DEFAULT_LOOK = { wallpaper: DEFAULT_WALLPAPER, dim: true, labels: true, revision: null };

function readStored() {
  try {
    return localStorage.getItem(STORAGE_KEY) || null;
  } catch {
    // Private browsing, or storage the browser refuses. The selection then
    // lasts for this page rather than for this device, which is a degraded
    // experience and not a broken one.
    return null;
  }
}

function writeStored(id) {
  try {
    if (id) localStorage.setItem(STORAGE_KEY, id);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // See `readStored`.
  }
}

/**
 * Where to fetch this desktop's own picture.
 *
 * The revision is in the query string so replacing the image repaints it. The
 * bytes are served with `no-cache`, so this is about the browser noticing a new
 * picture at the same address, not about caching policy.
 */
function customUrlFor(id, look) {
  if (!id || look?.wallpaper !== 'custom') return null;
  return `${desktopsApi.wallpaperUrl(id)}?v=${look.revision ?? 0}`;
}

export default function DesktopsProvider({ children }) {
  const [state, setState] = useState(() => ({
    desktops: [],
    defaultId: null,
    error: null,
    loaded: false,
  }));
  const [requested, setRequested] = useState(readStored);
  const [look, setLook] = useState(null);

  const load = useCallback(async () => {
    try {
      const payload = await desktopsApi.list();
      setState({
        desktops: payload.desktops || [],
        defaultId: payload.defaultId || null,
        error: null,
        loaded: true,
      });
      return payload;
    } catch (error) {
      // Keep whatever list is on screen: a failed refresh should not blank the
      // switcher while the user is reading it.
      setState((previous) => ({ ...previous, error, loaded: true }));
      return null;
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // The selected desktop is the requested one if it still exists, and the
  // first one otherwise. Resolving rather than storing means a deletion
  // elsewhere cannot leave this tab pointing at nothing.
  const selectedId = useMemo(() => {
    if (!state.desktops.length) return null;
    const found = state.desktops.some((desktop) => desktop.id === requested);
    return found ? requested : state.defaultId || state.desktops[0].id;
  }, [state.desktops, state.defaultId, requested]);

  const selected = useMemo(
    () => state.desktops.find((desktop) => desktop.id === selectedId) || null,
    [state.desktops, selectedId],
  );

  // Once the list is known, write back what was actually resolved, so a stale
  // stored id is corrected rather than re-resolved on every load.
  useEffect(() => {
    if (state.loaded && selectedId && selectedId !== requested) {
      writeStored(selectedId);
      setRequested(selectedId);
    }
  }, [state.loaded, selectedId, requested]);

  // Each desktop is dressed its own way, so the appearance is reloaded when the
  // selection changes rather than carried across.
  useEffect(() => {
    if (!selectedId) {
      setLook(null);
      return undefined;
    }
    let live = true;
    setLook(null);
    desktopsApi
      .appearance(selectedId)
      .then((payload) => {
        if (live) setLook(payload);
      })
      .catch(() => {
        // The desk still draws: without an answer it uses the default picture
        // rather than nothing at all.
        if (live) setLook(DEFAULT_LOOK);
      });
    return () => {
      live = false;
    };
  }, [selectedId]);

  const select = useCallback((id) => {
    writeStored(id);
    setRequested(id);
  }, []);

  const create = useCallback(
    async (name) => {
      const made = await desktopsApi.create(name);
      await load();
      // Making a desktop is asking to use it. Leaving the viewer on the old one
      // would mean the only sign anything happened is a longer menu.
      if (made?.id) select(made.id);
      return made;
    },
    [load, select],
  );

  const rename = useCallback(
    async (id, name, revision) => {
      const renamed = await desktopsApi.rename(id, name, revision);
      await load();
      return renamed;
    },
    [load],
  );

  const remove = useCallback(
    async (id) => {
      await desktopsApi.remove(id);
      const payload = await load();
      // Leaving the viewer on a workspace that no longer exists would show an
      // empty desk with no way back, so move to the first one that does.
      if (id === requested) select(payload?.defaultId || null);
      return true;
    },
    [load, requested, select],
  );

  /** Change how the selected desktop looks, and show it immediately. */
  const saveAppearance = useCallback(
    async (patch) => {
      if (!selectedId) return null;
      const saved = await desktopsApi.saveAppearance(selectedId, patch);
      setLook(saved);
      return saved;
    },
    [selectedId],
  );

  const uploadWallpaper = useCallback(
    async (file) => {
      if (!selectedId) return null;
      await desktopsApi.putWallpaper(selectedId, file);
      const saved = await desktopsApi.appearance(selectedId);
      setLook(saved);
      return saved;
    },
    [selectedId],
  );

  const removeWallpaper = useCallback(async () => {
    if (!selectedId) return null;
    await desktopsApi.deleteWallpaper(selectedId);
    const saved = await desktopsApi.appearance(selectedId);
    setLook(saved);
    return saved;
  }, [selectedId]);

  // What is open on the selected desktop. It lives here because the desk draws
  // the windows and the rail names them, and two copies would let the two
  // disagree about which one is in front.
  const views = useDesktopViews(selectedId);

  const appearance = useMemo(() => {
    const current = look || DEFAULT_LOOK;
    return { ...current, customUrl: customUrlFor(selectedId, current) };
  }, [look, selectedId]);

  const value = useMemo(
    () => ({
      desktops: state.desktops,
      defaultId: state.defaultId,
      loaded: state.loaded,
      error: state.error,
      selectedId,
      selected,
      select,
      create,
      rename,
      remove,
      refresh: load,
      appearance,
      appearanceLoaded: Boolean(look),
      saveAppearance,
      uploadWallpaper,
      removeWallpaper,
      views,
    }),
    [
      state,
      selectedId,
      selected,
      select,
      create,
      rename,
      remove,
      load,
      appearance,
      look,
      saveAppearance,
      uploadWallpaper,
      removeWallpaper,
      views,
    ],
  );

  return <DesktopsContext.Provider value={value}>{children}</DesktopsContext.Provider>;
}

const OUTSIDE = {
  desktops: [],
  defaultId: null,
  loaded: true,
  error: null,
  selectedId: null,
  selected: null,
  select: () => {},
  create: async () => null,
  rename: async () => null,
  remove: async () => false,
  refresh: async () => null,
  appearance: { ...DEFAULT_LOOK, customUrl: null },
  appearanceLoaded: false,
  saveAppearance: async () => null,
  uploadWallpaper: async () => null,
  removeWallpaper: async () => null,
  views: {
    views: [],
    ordered: [],
    loaded: false,
    layout: {
      revision: 0,
      arrangement: 'floating',
      maximizedView: null,
      primaryView: null,
      secondaryView: null,
      dividerRatio: 0.5,
      selectedView: null,
    },
    open: async () => null,
    close: async () => {},
    select: async () => {},
    patchView: () => {},
    minimize: () => {},
    restore: () => {},
    maximize: async () => null,
    saveLayout: async () => null,
    setArea: () => {},
    refresh: async () => null,
    flush: async () => {},
    windowMotion: null,
    toggleWindow: () => {},
    clearWindowMotion: () => {},
  },
};

/**
 * The desktops, the selected one and how it is dressed.
 *
 * A component outside the provider (a fixture, a test) gets an empty, loaded
 * state rather than an exception: discovering a missing provider is not a
 * widget's job.
 */
export function useDesktops() {
  return useContext(DesktopsContext) || OUTSIDE;
}
