import { useCallback, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  ArrowSquareOut,
  AppWindow as AppWindowIcon,
  GearSix,
  PlusCircle,
  PushPin,
  PushPinSlash,
  SquaresFour,
  Stop,
  Trash,
} from '@phosphor-icons/react';
import { useApps } from '../store.jsx';
import { api, isProcessApp } from '../api.js';
import { useResource } from '../hooks/useResource.js';
import { useDeveloperTools } from '../developer.js';
import useMediaQuery from '../hooks/useMediaQuery.js';
import useLongPress from '../hooks/useLongPress.js';
import { writeAppReference } from '../desktops/app-reference.js';
import useSwipe from '../hooks/useSwipe.js';
import { PHONE } from '../breakpoints.js';
import { coreApps } from '../navigation.js';
import { launchpadReturnTo } from '../shortcuts.js';
import { useWallpaperBody } from '../desk/wallpaper.js';
import WorkspacePage from '../components/WorkspacePage.jsx';
import GlobalSearch from '../components/GlobalSearch.jsx';
import AppIcon from '../components/AppIcon.jsx';
import ContextMenu from '../components/ui/ContextMenu.jsx';
import AppSettingsDrawer from '../components/AppSettingsDrawer.jsx';
import Dialog from '../components/ui/Dialog.jsx';
import Button from '../components/ui/Button.jsx';
import { useSettingsPopup } from '../components/SettingsProvider.jsx';
import { addAppWidgetToDesk, firstWidget } from '../desk/addAppWidget.js';
import { useDesktops } from '../desktops/DesktopsProvider.jsx';
import { hasUpdate } from './Library.jsx';
import { automationsApi } from '../automationsApi.js';
import { formatBytes } from '../api.js';

// The tabs, in the order they are shown. Frequent stays hidden until there is
// enough history for it to say anything true, so a new Vela does not offer a
// tab that would sit empty or, worse, rank three opens as a habit.
const TABS = [
  { key: 'all', label: 'All' },
  { key: 'frequent', label: 'Frequent' },
  { key: 'running', label: 'Running' },
  { key: 'updates', label: 'Updates' },
];
const FREQUENT_MIN_OPENS = 5;
const FREQUENT_MAX = 12;

// The full-screen app grid. Every installed app, Vela's own tools, and a way to
// get more, laid out as big icons over the blurred wallpaper. The hero search
// filters the grid live and Enter opens the first match; a tile opens on click
// and offers its actions on right-click, Shift+F10 or a long press.
// `onClose` is how the overlay host dismisses it. Without one — a page that
// rendered it directly — it falls back to the route it was opened from, which
// is what it always did.
export default function Launchpad({ onClose }) {
  const { apps, openApp, runAction, pushToast, pinned, pinApp, unpinApp, refreshApps } = useApps();
  const developer = useDeveloperTools();
  const navigate = useNavigate();
  const { openSettings } = useSettingsPopup();
  const dismiss = useCallback(
    () => (onClose ? onClose() : navigate(launchpadReturnTo())),
    [onClose, navigate],
  );
  const phone = useMediaQuery(PHONE);
  // The Launchpad floats over the same picture as the desk, so it reads the
  // selected desktop's appearance rather than keeping its own copy.
  const { appearance: desk, selectedId, views } = useDesktops();
  // What this computer is called, if the user named it. The Launchpad says it
  // at the top on a phone, where the desk's status strip cannot fit.
  const loadSettings = useCallback((options) => api.getSettings(options), []);
  const { data: settingsData } = useResource(loadSettings);
  const serverName = settingsData?.identity?.serverName || '';

  // The wallpaper flags are set the same way the desk sets them, so the shell
  // draws the picture and the rail takes its on-wallpaper ink.
  useWallpaperBody(desk);

  // The tab rides in the URL, so a Launchpad opened on Updates can be linked to
  // and survives a reload the way the Marketplace's tabs do.
  const [params, setParams] = useSearchParams();
  const requested = params.get('tab');
  const [query, setQuery] = useState('');
  const [menu, setMenu] = useState(null); // { item, x, y }
  const [settingsApp, setSettingsApp] = useState(null);
  const [removing, setRemoving] = useState(null);
  const gridRef = useRef(null);
  const menuOpener = useRef(null);

  // Which apps say they need attention, read from the summaries they publish
  // themselves — the same signal the rail uses for its amber dot.
  const loadSummaries = useCallback((options) => api.appWidgets(options), []);
  const { data: summaryData } = useResource(loadSummaries, { intervalMs: 60000 });
  const attention = useMemo(() => {
    const ids = new Set();
    for (const entry of summaryData?.widgets || []) {
      if (entry?.summary?.attention && !entry.snoozedUntil) ids.add(entry.appId);
    }
    return ids;
  }, [summaryData]);

  // Which icons carry a count. An app publishes it on a widget summary, so the
  // first summary of its that has one wins — an app with several widgets badges
  // its icon once rather than fighting with itself.
  const badges = useMemo(() => {
    const found = new Map();
    for (const entry of summaryData?.widgets || []) {
      const badge = entry?.summary?.badge;
      if (badge && !found.has(entry.appId)) found.set(entry.appId, badge);
    }
    return found;
  }, [summaryData]);

  // Open counts for Frequent. Read once when the Launchpad opens: a tab that
  // reorders itself while being looked at would move the tile under the cursor.
  const loadUsage = useCallback((options) => api.usage(options), []);
  const { data: usageData } = useResource(loadUsage);
  const opens = useMemo(() => usageData?.totals || {}, [usageData]);

  const installed = useMemo(
    () =>
      (apps || [])
        .filter((app) => app.installed)
        .slice()
        .sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id)),
    [apps],
  );

  const updatable = useMemo(() => installed.filter(hasUpdate), [installed]);

  // Vela's own tools carry counts too, from what Vela itself knows rather than
  // from a published summary: how many apps have an update waiting, and how many
  // automations failed today.
  const loadFlows = useCallback((options) => automationsApi.status(options), []);
  const { data: flows } = useResource(loadFlows, { intervalMs: 60000 });
  const coreBadge = useCallback(
    (id) => {
      if (id === 'library' && updatable.length) return String(Math.min(updatable.length, 99));
      if (id === 'automations' && flows?.failuresToday)
        return String(Math.min(flows.failuresToday, 99));
      return '';
    },
    [updatable, flows],
  );

  // Vela's own tools drawn as tiles. `glyph` lets AppIcon render the tool's
  // Phosphor mark inside the same tinted tile an app gets.
  const core = useMemo(
    () =>
      coreApps(developer).map((entry) => ({
        kind: 'core',
        id: entry.id,
        name: entry.label,
        to: entry.to,
        popup: entry.popup,
        badge: coreBadge(entry.id),
        appLike: { id: entry.id, name: entry.label, glyph: entry.icon, color: entry.color },
      })),
    [developer, coreBadge],
  );

  const appItems = useMemo(
    () =>
      installed.map((app) => ({
        kind: 'app',
        id: app.id,
        name: app.name,
        app,
        badge: badges.get(app.id) || '',
      })),
    [installed, badges],
  );
  const openItems = useMemo(() => appItems.filter((item) => item.app.running), [appItems]);
  const updateItems = useMemo(() => appItems.filter((item) => hasUpdate(item.app)), [appItems]);

  // Frequent ranks Vela's own tools beside apps, because "what do I open" does
  // not distinguish them. It stays hidden until there is enough history for the
  // ranking to mean something.
  const frequentItems = useMemo(() => {
    const ranked = [...appItems, ...core]
      .map((item) => ({ item, count: opens[item.id] || 0 }))
      .filter((entry) => entry.count > 0)
      .sort((a, b) => b.count - a.count || a.item.name.localeCompare(b.item.name));
    return ranked.slice(0, FREQUENT_MAX).map((entry) => entry.item);
  }, [appItems, core, opens]);

  const totalOpens = useMemo(
    () => Object.values(opens).reduce((sum, count) => sum + count, 0),
    [opens],
  );
  const frequentReady = totalOpens >= FREQUENT_MIN_OPENS && frequentItems.length > 0;

  const tabs = useMemo(
    () => TABS.filter((entry) => entry.key !== 'frequent' || frequentReady),
    [frequentReady],
  );
  const tab = tabs.some((entry) => entry.key === requested) ? requested : 'all';

  // The phone's answer to the desk's status strip: what is running and what
  // this computer has moved today.
  const loadMetrics = useCallback((options) => api.systemMetrics(options), []);
  const { data: metrics } = useResource(loadMetrics, { enabled: phone, intervalMs: 60000 });
  const serverLine = useMemo(() => {
    const running = openItems.length;
    const today = metrics?.network?.today?.total;
    return [
      serverName,
      running ? `${running} ${running === 1 ? 'app' : 'apps'} running` : '',
      today ? `${formatBytes(today)} today` : '',
    ]
      .filter(Boolean)
      .join(' · ');
  }, [serverName, openItems, metrics]);

  const needle = query.trim().toLowerCase();
  const matches = useCallback(
    (item) => !needle || item.name.toLowerCase().includes(needle),
    [needle],
  );

  // Each tab is a different cut of the same set. All keeps the sections it has
  // always had; the others are one list, because a Frequent tab split into
  // "Apps" and "Vela" would undo the ranking that is the point of it.
  const sections = useMemo(() => {
    if (tab === 'frequent') {
      const items = frequentItems.filter(matches);
      return items.length ? [{ key: 'frequent', label: 'Most opened', items }] : [];
    }
    if (tab === 'running') {
      const items = openItems.filter(matches);
      return items.length ? [{ key: 'running', label: 'Running', items }] : [];
    }
    if (tab === 'updates') {
      const items = updateItems.filter(matches);
      return items.length ? [{ key: 'updates', label: 'Ready to update', items }] : [];
    }
    const open = openItems.filter(matches);
    const all = appItems.filter(matches);
    const vela = core.filter(matches);
    const list = [];
    if (open.length) list.push({ key: 'open', label: 'Open', items: open });
    if (all.length) list.push({ key: 'apps', label: 'Apps', items: all });
    if (vela.length) list.push({ key: 'vela', label: 'Vela', items: vela });
    return list;
  }, [tab, frequentItems, openItems, updateItems, appItems, core, matches]);

  // What a tab says when it has nothing in it, which is a different sentence
  // from "your search found nothing".
  const emptyTab = {
    running: 'No apps are running.',
    updates: 'Every app is up to date.',
    frequent: 'Nothing opened yet.',
  }[tab];

  const flatItems = useMemo(() => sections.flatMap((section) => section.items), [sections]);
  const nothing = needle && flatItems.length === 0;

  const openItem = useCallback(
    (item) => {
      if (item.kind === 'app') {
        openApp(item.id, { returnTo: '/apps' });
        return;
      }
      // `openApp` counts an app open for Frequent; a core tool is opened here,
      // so it is counted here for the same tab to rank them together.
      api.recordUsage(item.id).catch(() => {});
      if (item.popup) {
        openSettings();
        return;
      }
      navigate(item.to, { state: { returnTo: '/apps' } });
    },
    [openApp, openSettings, navigate],
  );

  const iconSize = phone ? 52 : 72;

  // --------------------------------------------------------------- the menu

  const addWidgetToDesk = useCallback(
    (app) => addAppWidgetToDesk(app, pushToast, selectedId),
    [pushToast, selectedId],
  );

  // Opening an app as a window on the desktop being looked at. It starts the
  // app if it is not running: a window with nothing in it is not an answer.
  const openWindow = useCallback(
    async (app) => {
      try {
        // Starting the app and opening a view are two things: a window with
        // nothing running in it is not an answer, and a running app with no
        // window is not what was asked for.
        if (isProcessApp(app) && !app.running) {
          await api.launch(app.id);
          await refreshApps();
        }
        await views.open({ kind: 'app', appId: app.id, title: app.name });
        api.recordUsage(app.id).catch(() => {});
        dismiss();
        navigate('/');
      } catch (error) {
        pushToast(error.message || `Could not open ${app.name}.`, 'error');
      }
    },
    [refreshApps, views, dismiss, navigate, pushToast],
  );

  const menuItems = useMemo(() => {
    if (!menu) return [];
    const item = menu.item;
    const items = [{ label: 'Open', icon: SquaresFour, onSelect: () => openItem(item) }];
    if (item.kind === 'app') {
      // A window on the desktop, rather than the full-screen page. The two are
      // different presentations: a window can be minimized and left running
      // while you do something else, and it belongs to the desktop you are on.
      items.push({
        label: 'Open in a window',
        icon: AppWindowIcon,
        onSelect: () => openWindow(item.app),
      });
    }
    // Both apps and core tools can be pinned to the rail.
    if (pinned.includes(item.id)) {
      items.push({
        label: 'Unpin from rail',
        icon: PushPinSlash,
        onSelect: () => unpinApp(item.id),
      });
    } else {
      items.push({ label: 'Pin to rail', icon: PushPin, onSelect: () => pinApp(item.id) });
    }
    if (item.kind === 'app') {
      const app = item.app;
      if (firstWidget(app)) {
        items.push({
          label: 'Add widget to desk',
          icon: PlusCircle,
          onSelect: () => addWidgetToDesk(app),
        });
      }
      items.push({
        label: 'App settings',
        icon: GearSix,
        onSelect: () => setSettingsApp(app),
      });
      if (app.running && isProcessApp(app)) {
        items.push({ label: 'Stop', icon: Stop, onSelect: () => runAction(app.id, 'stop') });
      }
      items.push({ separator: true });
      items.push({
        label: 'Remove',
        icon: Trash,
        danger: true,
        onSelect: () => setRemoving(app),
      });
    } else {
      items.push({
        label: 'Open in this window',
        icon: ArrowSquareOut,
        onSelect: () => openItem(item),
      });
    }
    return items;
  }, [menu, openItem, openWindow, addWidgetToDesk, runAction, pinned, pinApp, unpinApp]);

  const openMenuAt = useCallback((item, x, y, opener) => {
    menuOpener.current = opener || null;
    setMenu({ item, x, y });
  }, []);

  // Dragging an app tile carries a typed reference to the app — its identity,
  // never its contents — so only a surface that knows what to do with one takes
  // the drop. The desk board makes a widget of it; a task composer attaches it
  // as something to talk about. Neither is permission to do anything else.
  const onTileDragStart = (item) => (event) => {
    writeAppReference(event.dataTransfer, item, 'launchpad');
  };

  const onTileContextMenu = (item) => (event) => {
    event.preventDefault();
    openMenuAt(item, event.clientX, event.clientY, event.currentTarget);
  };

  const onTileKeyDown = (event) => {
    // Shift+F10 is the keyboard's context-menu request; the menu opens at the
    // tile so focus lands somewhere visible.
    if (event.shiftKey && event.key === 'F10') {
      event.preventDefault();
      const tile = event.currentTarget;
      const box = tile.getBoundingClientRect();
      const item = flatItems.find((entry) => entry.id === tile.dataset.item);
      if (item) openMenuAt(item, box.left + box.width / 2, box.bottom, tile);
      return;
    }
    // Arrow keys roam the grid. Left/Right step through the flat order;
    // Up/Down find the geometrically nearest tile in that direction, which is
    // correct however many columns the responsive grid settled on.
    const tiles = Array.from(gridRef.current?.querySelectorAll('.launch-tile') || []);
    const index = tiles.indexOf(event.currentTarget);
    if (index < 0) return;
    let next = null;
    if (event.key === 'ArrowRight') next = tiles[index + 1];
    else if (event.key === 'ArrowLeft') next = tiles[index - 1];
    else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      const box = event.currentTarget.getBoundingClientRect();
      const want = event.key === 'ArrowDown' ? 1 : -1;
      let best = null;
      let bestScore = Infinity;
      for (const tile of tiles) {
        if (tile === event.currentTarget) continue;
        const other = tile.getBoundingClientRect();
        const dy = other.top - box.top;
        if (Math.sign(dy) !== want || Math.abs(dy) < 4) continue;
        const score = Math.abs(dy) + Math.abs(other.left - box.left) * 2;
        if (score < bestScore) {
          bestScore = score;
          best = tile;
        }
      }
      next = best;
    }
    if (next) {
      event.preventDefault();
      next.focus();
    }
  };

  // On a phone, a swipe down from the top closes the Launchpad — but only when
  // the grid is already scrolled to the top, so a normal scroll is untouched.
  const swipeDown = useSwipe({
    onDown: () => {
      const content = document.querySelector('.launchpad-workspace .workspace-content');
      if (!content || content.scrollTop <= 2) dismiss();
    },
  });

  const longPress = useLongPress(({ x, y, target }) => {
    const tile = target.closest?.('.launch-tile');
    if (!tile) return;
    const item = flatItems.find((entry) => entry.id === tile.dataset.item);
    if (item) openMenuAt(item, x, y, tile);
  });

  const tile = (item, tabbable) => {
    const dot = item.kind === 'app' && item.app.running;
    const flag = item.kind === 'app' && attention.has(item.id);
    // The badge is drawn on the icon, which is decorative, so the count is said
    // once here instead — "Notes, 3" rather than a number no reader reaches.
    const label = item.badge ? `${item.name}, ${item.badge}` : item.name;
    return (
      <button
        key={`${item.kind}-${item.id}`}
        type="button"
        className="launch-tile"
        data-item={item.id}
        data-badge={item.badge || undefined}
        role="gridcell"
        tabIndex={tabbable ? 0 : -1}
        aria-label={label}
        draggable={item.kind === 'app'}
        onDragStart={item.kind === 'app' ? onTileDragStart(item) : undefined}
        onClick={() => openItem(item)}
        onContextMenu={onTileContextMenu(item)}
        onKeyDown={onTileKeyDown}
        {...longPress}
      >
        <span className="launch-icon">
          <AppIcon
            app={item.kind === 'core' ? item.appLike : item.app}
            size={iconSize}
            badge={item.badge}
          />
          {dot ? <span className="launch-dot launch-dot-running" aria-hidden="true" /> : null}
          {flag ? <span className="launch-dot launch-dot-attention" aria-hidden="true" /> : null}
          {item.kind === 'core' ? (
            <span className="launch-vela" aria-hidden="true">
              <img src="/vela-mark.png" alt="" />
            </span>
          ) : null}
        </span>
        <span className="launch-label">{item.name}</span>
      </button>
    );
  };

  let tabbableAssigned = false;
  const renderTile = (item) => {
    const tabbable = !tabbableAssigned;
    tabbableAssigned = true;
    return tile(item, tabbable);
  };

  return (
    <WorkspacePage search={false} scroll className="launchpad-workspace">
      <div
        className="launchpad"
        onPointerDown={phone ? swipeDown.onPointerDown : undefined}
        onPointerMove={phone ? swipeDown.onPointerMove : undefined}
        onPointerUp={phone ? swipeDown.onPointerUp : undefined}
        onPointerCancel={phone ? swipeDown.onPointerCancel : undefined}
        onKeyDown={(event) => {
          // Escape leaves All apps the way it was opened: back to the page
          // underneath, with focus on whatever asked for it. The context menu
          // owns Escape while it is open, so this only fires from the grid.
          if (event.key === 'Escape' && !menu) {
            event.preventDefault();
            dismiss();
          }
        }}
      >
        <div className="launchpad-backdrop" aria-hidden="true" />
        <div className="launchpad-inner">
          {/* On a phone there is no room for the desk's status strip, so the
              one line worth carrying over says it here instead. */}
          {phone && serverLine && (
            <p className="launchpad-server" role="status">
              {serverLine}
            </p>
          )}

          <div className="launchpad-hero">
            <GlobalSearch
              variant="hero"
              value={query}
              onQueryChange={setQuery}
              showResults={false}
              autoFocus
              placeholder={`Type to filter ${appItems.length} app${appItems.length === 1 ? '' : 's'}`}
              onEnter={() => {
                if (flatItems[0]) openItem(flatItems[0]);
              }}
            />
          </div>

          <div className="launch-tabs" role="tablist" aria-label="Which apps">
            {tabs.map((entry) => (
              <button
                key={entry.key}
                type="button"
                role="tab"
                className={`launch-tab${tab === entry.key ? ' is-active' : ''}`}
                aria-selected={tab === entry.key}
                onClick={() =>
                  setParams(entry.key === 'all' ? {} : { tab: entry.key }, { replace: true })
                }
              >
                {entry.label}
                {entry.key === 'updates' && updatable.length ? (
                  <span className="launch-tab-count">{updatable.length}</span>
                ) : null}
                {entry.key === 'running' && openItems.length ? (
                  <span className="launch-tab-count">{openItems.length}</span>
                ) : null}
              </button>
            ))}
          </div>

          {apps === null ? (
            <div className="launch-grid" aria-hidden="true">
              {Array.from({ length: 12 }).map((_, i) => (
                <div key={i} className="launch-tile launch-tile-skeleton" />
              ))}
            </div>
          ) : installed.length === 0 && !needle ? (
            <p className="launch-empty" role="status">
              No apps yet.{' '}
              <button type="button" className="linklike" onClick={() => navigate('/library')}>
                Open the Marketplace
              </button>{' '}
              to add one.
            </p>
          ) : nothing ? (
            <p className="launch-empty" role="status">
              No app matches “{query.trim()}”.
            </p>
          ) : sections.length === 0 && emptyTab ? (
            <p className="launch-empty" role="status">
              {emptyTab}
            </p>
          ) : (
            <div className="launch-sections" ref={gridRef} role="grid" aria-label="Apps">
              {sections.map((section) => (
                <section key={section.key} className="launch-section" role="row">
                  <h2 className="launch-section-label">{section.label}</h2>
                  <div className="launch-grid">{section.items.map(renderTile)}</div>
                </section>
              ))}
              {!needle && tab === 'all' && (
                <section className="launch-section" role="row">
                  <h2 className="launch-section-label">Get more apps</h2>
                  <div className="launch-grid">
                    <button
                      type="button"
                      className="launch-tile launch-tile-more"
                      onClick={() => navigate('/library')}
                    >
                      <span className="launch-icon">
                        <span
                          className="launch-more-mark"
                          style={{ width: iconSize, height: iconSize }}
                        >
                          <PlusCircle size={Math.round(iconSize * 0.5)} />
                        </span>
                      </span>
                      <span className="launch-label">Marketplace</span>
                    </button>
                  </div>
                </section>
              )}
            </div>
          )}
        </div>
      </div>

      <ContextMenu
        open={Boolean(menu)}
        x={menu?.x || 0}
        y={menu?.y || 0}
        items={menuItems}
        label={menu ? `${menu.item.name} actions` : 'Actions'}
        returnFocusRef={menuOpener}
        onClose={() => setMenu(null)}
      />

      {settingsApp && <AppSettingsDrawer app={settingsApp} onClose={() => setSettingsApp(null)} />}

      <Dialog
        open={Boolean(removing)}
        aria-labelledby="launch-remove-title"
        onClose={() => setRemoving(null)}
      >
        <h2 id="launch-remove-title">Remove {removing?.name}?</h2>
        <p>This uninstalls the app from this computer. Its data is removed too.</p>
        <div className="form-actions">
          <Button
            className="btn-danger"
            onClick={() => {
              runAction(removing.id, 'uninstall');
              setRemoving(null);
            }}
          >
            Remove
          </Button>
          <Button onClick={() => setRemoving(null)}>Cancel</Button>
        </div>
      </Dialog>
    </WorkspacePage>
  );
}
