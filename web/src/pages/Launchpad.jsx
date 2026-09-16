import { useCallback, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ArrowSquareOut,
  GearSix,
  PlusCircle,
  PushPin,
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
import { deskApi } from '../desk/useDeskBoards.js';
import { widgetsOf, withWidgets, colsOf } from '../desk/boards.js';
import { compact, findFreeSpot, nextWidgetId } from '../desk/grid/layout.js';
import { appWidgetTypeId, APP_WIDGET_SIZES } from '../desk/registry.js';

// The desk's wallpaper preferences also dress the Launchpad, which floats over
// the same blurred picture. Reading them here keeps the two surfaces in step
// without threading a prop through the shell.
function useDeskPrefs() {
  const load = useCallback((options) => api.getSettings(options), []);
  const { data } = useResource(load);
  return data?.desk || { wallpaper: 'lake', dim: true };
}

// The first widget an app declares, if any — what "Add widget to desk" places.
function firstWidget(app) {
  const declared = Array.isArray(app?.widgets) ? app.widgets[0] : null;
  return declared && typeof declared.id === 'string' ? declared : null;
}

// The full-screen app grid. Every installed app, Vela's own tools, and a way to
// get more, laid out as big icons over the blurred wallpaper. The hero search
// filters the grid live and Enter opens the first match; a tile opens on click
// and offers its actions on right-click, Shift+F10 or a long press.
export default function Launchpad() {
  const { apps, openApp, runAction, pushToast } = useApps();
  const developer = useDeveloperTools();
  const navigate = useNavigate();
  const { openSettings } = useSettingsPopup();
  const phone = useMediaQuery(PHONE);
  const desk = useDeskPrefs();

  // The wallpaper flags are set the same way the desk sets them, so the shell
  // draws the picture and the rail takes its on-wallpaper ink.
  useWallpaperBody(desk);

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
      if (entry?.summary?.attention) ids.add(entry.appId);
    }
    return ids;
  }, [summaryData]);

  const installed = useMemo(
    () =>
      (apps || [])
        .filter((app) => app.installed)
        .slice()
        .sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id)),
    [apps],
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
        appLike: { id: entry.id, name: entry.label, glyph: entry.icon, color: entry.color },
      })),
    [developer],
  );

  const appItems = useMemo(
    () => installed.map((app) => ({ kind: 'app', id: app.id, name: app.name, app })),
    [installed],
  );
  const openItems = useMemo(() => appItems.filter((item) => item.app.running), [appItems]);

  const needle = query.trim().toLowerCase();
  const matches = useCallback(
    (item) => !needle || item.name.toLowerCase().includes(needle),
    [needle],
  );

  const sections = useMemo(() => {
    const open = openItems.filter(matches);
    const all = appItems.filter(matches);
    const vela = core.filter(matches);
    const list = [];
    if (open.length) list.push({ key: 'open', label: 'Open', items: open });
    if (all.length) list.push({ key: 'apps', label: 'Apps', items: all });
    if (vela.length) list.push({ key: 'vela', label: 'Vela', items: vela });
    return list;
  }, [openItems, appItems, core, matches]);

  const flatItems = useMemo(() => sections.flatMap((section) => section.items), [sections]);
  const nothing = needle && flatItems.length === 0;

  const openItem = useCallback(
    (item) => {
      if (item.kind === 'app') {
        openApp(item.id, { returnTo: '/apps' });
        return;
      }
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
    async (app) => {
      const declared = firstWidget(app);
      if (!declared) return;
      try {
        const payload = await deskApi.load();
        const boards = payload.boards;
        const key = 'desktop';
        const current = widgetsOf(boards, key);
        const cols = colsOf(boards, key);
        const [w, h] = APP_WIDGET_SIZES[declared.size] || APP_WIDGET_SIZES.m;
        const width = Math.min(w, cols);
        const placed = {
          i: nextWidgetId(current),
          type: appWidgetTypeId(app.id, declared.id),
          ...findFreeSpot(current, width, h, cols),
          w: width,
          h,
          cfg: {},
        };
        const next = withWidgets(boards, key, compact([...current, placed]));
        await deskApi.save(payload.revision, next);
        pushToast(`Added ${app.name} to your desk.`, 'success');
      } catch (error) {
        pushToast(error.message || 'Could not add the widget.', 'error');
      }
    },
    [pushToast],
  );

  const menuItems = useMemo(() => {
    if (!menu) return [];
    const item = menu.item;
    const items = [{ label: 'Open', icon: SquaresFour, onSelect: () => openItem(item) }];
    // Pinning the rail arrives with the core-apps stage; the entry is shown so
    // the menu's shape is stable, and it is enabled once the server stores pins.
    items.push({ label: 'Pin to rail', icon: PushPin, disabled: true });
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
  }, [menu, openItem, addWidgetToDesk, runAction]);

  const openMenuAt = useCallback((item, x, y, opener) => {
    menuOpener.current = opener || null;
    setMenu({ item, x, y });
  }, []);

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

  const longPress = useLongPress(({ x, y, target }) => {
    const tile = target.closest?.('.launch-tile');
    if (!tile) return;
    const item = flatItems.find((entry) => entry.id === tile.dataset.item);
    if (item) openMenuAt(item, x, y, tile);
  });

  const tile = (item, tabbable) => {
    const dot = item.kind === 'app' && item.app.running;
    const flag = item.kind === 'app' && attention.has(item.id);
    return (
      <button
        key={`${item.kind}-${item.id}`}
        type="button"
        className="launch-tile"
        data-item={item.id}
        role="gridcell"
        tabIndex={tabbable ? 0 : -1}
        aria-label={item.name}
        onClick={() => openItem(item)}
        onContextMenu={onTileContextMenu(item)}
        onKeyDown={onTileKeyDown}
        {...longPress}
      >
        <span className="launch-icon">
          <AppIcon app={item.kind === 'core' ? item.appLike : item.app} size={iconSize} />
          {dot ? <span className="launch-dot launch-dot-running" aria-hidden="true" /> : null}
          {flag ? <span className="launch-dot launch-dot-attention" aria-hidden="true" /> : null}
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
        onKeyDown={(event) => {
          // Escape leaves the Launchpad the way it was opened — back to the
          // route the user came from. The context menu owns Escape while it is
          // open, so this only fires from the grid itself.
          if (event.key === 'Escape' && !menu) {
            event.preventDefault();
            navigate(launchpadReturnTo());
          }
        }}
      >
        <div className="launchpad-backdrop" aria-hidden="true" />
        <div className="launchpad-inner">
          <div className="launchpad-hero">
            <GlobalSearch
              variant="hero"
              value={query}
              onQueryChange={setQuery}
              showResults={false}
              autoFocus
              onEnter={() => {
                if (flatItems[0]) openItem(flatItems[0]);
              }}
            />
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
                Open the Library
              </button>{' '}
              to add one.
            </p>
          ) : nothing ? (
            <p className="launch-empty" role="status">
              No app matches “{query.trim()}”.
            </p>
          ) : (
            <div className="launch-sections" ref={gridRef} role="grid" aria-label="Apps">
              {sections.map((section) => (
                <section key={section.key} className="launch-section" role="row">
                  <h2 className="launch-section-label">{section.label}</h2>
                  <div className="launch-grid">{section.items.map(renderTile)}</div>
                </section>
              ))}
              {!needle && (
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
                      <span className="launch-label">Library</span>
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
