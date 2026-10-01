// The windows open on a desktop, drawn over the desk.
//
// It measures the space it has rather than assuming it, so the rail's width,
// the status strip's height and the safe-area insets can all move without the
// stored geometry changing meaning. Where each window goes is
// `window-state.js`'s answer; this renders it and turns gestures back into
// patches.
//
// Minimize, maximize and close are three different things here, and stay three
// different things all the way down: minimize is presentation, maximize is the
// arrangement, and close is the only one that ends a view.
import { useCallback, useEffect, useRef, useState } from 'react';
import AppIcon from '../components/AppIcon.jsx';
import { useSettingsPopup } from '../components/SettingsProvider.jsx';
import Settings from '../pages/Settings.jsx';
import { coreById } from '../navigation.js';
import { hostSurfaceName } from '../shell/focus.js';
import { useApps } from '../store.jsx';
import AgentWindow from './AgentWindow.jsx';
import AppWindow from './AppWindow.jsx';
import EmptyPane from './EmptyPane.jsx';
import SplitDivider from './SplitDivider.jsx';
import WindowFrame from './WindowFrame.jsx';
import GenieOverlay from './motion/GenieOverlay.jsx';
import useWindowMotion from './motion/useWindowMotion.js';
import useFrameFocus from './useFrameFocus.js';
import { anchorFor } from './motion/anchors.js';
import { fallbackStyle } from './motion/genie-fallback.js';
import { panes, previewBounds, snapTargetFor } from './snap.js';
import {
  SPLIT_MIN_WIDTH,
  placeView,
  restorePatch,
  splitBounds,
  stackLayers,
  workArea,
} from './window-state.js';
import { useConfirm } from '../hooks/useConfirm.js';

/** What a window is when nothing said otherwise. Mirrors `DEFAULT_WINDOW`
 *  in `vela/manifest.py`, which is what the engine fills in for every app. */
const DEFAULT_WINDOW = { resizable: true, maximizable: true };

// Vela's own screens, when they open as windows. Settings asks for the size a
// settings window has on a desktop OS: room for the list and a section beside
// it, clamped like any other to the screen in front of somebody.
const HOST_WINDOWS = {
  settings: { ...DEFAULT_WINDOW, defaultSize: { width: 980, height: 680 } },
};

/**
 * How this view's window behaves, from the app that owns it.
 *
 * A view that is not an app — the agent window, a host surface, a site — has
 * no manifest to ask, and neither has an app whose summary has not arrived
 * yet, so both get the ordinary window. Falling back to "no options" rather
 * than to "fixed" matters: a summary that is still loading must not briefly
 * take a window's grips away.
 */
function windowOptionsFor(view, apps) {
  if (view.kind === 'host') return HOST_WINDOWS[view.surface] || DEFAULT_WINDOW;
  if (view.kind !== 'app') return DEFAULT_WINDOW;
  return apps?.find((app) => app.id === view.appId)?.view?.window || DEFAULT_WINDOW;
}

/** What to call a view that has not been given a title. */
function labelFor(view, apps) {
  if (view.title) return view.title;
  if (view.kind === 'app') {
    return apps?.find((app) => app.id === view.appId)?.name || 'App';
  }
  if (view.kind === 'host') return hostSurfaceName(view.surface);
  if (view.kind === 'agent') return 'Agent';
  if (view.kind === 'web') {
    try {
      return new URL(view.url).hostname;
    } catch {
      return 'Web';
    }
  }
  return 'Agent';
}

/** The mark a window carries in its title bar: the app's, or Vela's own tool's. */
function iconFor(view, apps, name) {
  if (view.kind === 'app') {
    return (
      <AppIcon
        app={apps?.find((app) => app.id === view.appId) || { id: view.appId, name }}
        size={20}
      />
    );
  }
  const core = view.kind === 'host' ? coreById(view.surface) : null;
  if (!core) return null;
  return (
    <AppIcon
      app={{ id: core.id, name: core.label, glyph: core.icon, color: core.color }}
      size={20}
    />
  );
}

export default function DesktopViewHost({ views, desktop }) {
  const { apps } = useApps();
  const { sectionRequest } = useSettingsPopup() || {};
  const host = useRef(null);
  const [area, setArea] = useState({ width: 0, height: 0 });
  // A window being dragged is drawn from this rather than from the server copy,
  // so the pointer is followed at the refresh rate and the write happens once.
  const [dragging, setDragging] = useState(null);
  // Unsaved work lives inside the app's frame; the app tells the host about it
  // and the host is what asks before closing.
  const [dirty, setDirty] = useState({});
  // A cold launch's wait belongs to the window's chrome: the app inside says
  // it is still starting and the frame draws the hairline and the label.
  const [busy, setBusy] = useState({});
  const confirm = useConfirm();
  // Which half a drag is currently offering, and the divider position being
  // previewed. Both are local: the person is still deciding.
  const [snapping, setSnapping] = useState(null);
  const [ratioPreview, setRatioPreview] = useState(null);
  const frames = useRef(new Map());
  // Presentation only. None of this touches an app's process, its session or an
  // agent's task: a minimized remote view is a view the owner is not looking
  // at, and the page it was showing is still open, still observed and still
  // being worked in.
  const motion = useWindowMotion({ desktopId: views.desktopId, desktop, area });

  useEffect(() => {
    if (!host.current) return undefined;
    const measure = () => {
      const next = workArea(host.current.getBoundingClientRect());
      setArea(next);
      views.setArea(next);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(host.current);
    return () => observer.disconnect();
  }, [views]);

  const frameFor = useCallback((viewId) => {
    if (!frames.current.has(viewId)) frames.current.set(viewId, { current: null });
    return frames.current.get(viewId);
  }, []);

  const onMove = useCallback(
    (viewId) =>
      (bounds, options = {}) => {
        if (bounds) {
          setDragging({ id: viewId, bounds });
          views.patchView(viewId, { bounds });
        }
        if (options.commit) setDragging(null);
      },
    [views],
  );

  // Where the pointer is during a title-bar drag, turned into an offer of a
  // half. Dropping while an offer is showing takes it; dropping anywhere else
  // leaves the window exactly where the drag put it.
  const onDragPoint = useCallback(
    (viewId) =>
      (point, options = {}) => {
        if (point) {
          const view = views.ordered.find((entry) => entry.id === viewId);
          const snappable = !view || windowOptionsFor(view, apps).resizable !== false;
          setSnapping({ id: viewId, side: snappable ? snapTargetFor(point, area) : null });
          return;
        }
        const offered = snapping?.id === viewId ? snapping.side : null;
        setSnapping(null);
        if (offered && options.drop) views.snap(viewId, offered);
      },
    [apps, area, snapping, views],
  );

  // Put a window away, or bring it back, with the warp where one is possible.
  // The layout change happens first and unconditionally — somebody asked for
  // the window to be minimized, so it is minimized — and the picture follows.
  const putAway = useCallback(
    (view, index) => {
      const minimized = Boolean(view.window?.minimized);
      // Where the window is, or where it is about to be. A minimized window is
      // not placed anywhere at all — that is what minimized means — so asking
      // the arrangement for it would answer "nowhere", and a motion out of
      // nowhere is no motion. What it is coming back to is the same rectangle
      // the restore itself will use.
      const size = windowOptionsFor(view, apps).defaultSize || null;
      const bounds = minimized
        ? restorePatch(view, area, index, size).bounds
        : placeView(view, { layout: views.layout, area, index, size });
      motion.animate(view, minimized ? 'expand' : 'collapse', {
        icon: anchorFor(view.id, host.current),
        bounds,
        apply: () => (minimized ? views.restore(view, index, size) : views.minimize(view)),
      });
    },
    [apps, area, motion, views],
  );

  // Somebody asked for a window to be put away or brought back from somewhere
  // that cannot measure the screen — the rail, a shortcut, an app being opened
  // again. This is where it actually happens, because this is where the work
  // area and the rail icon's place are known.
  const asked = views.windowMotion;
  // What to do it with, read at the moment of doing it. Held in a ref because
  // both of these change identity on every render: depending on them would run
  // this effect again and again, and an ask performed twice is a window put
  // away and immediately brought back.
  const perform = useRef({ putAway, views });
  perform.current = { putAway, views };
  const handled = useRef(0);
  useEffect(() => {
    // Nothing can be aimed at a screen that has not been measured yet, so an
    // ask that arrives with a page — from the rail, on another route — waits
    // for the first measurement rather than being dropped.
    if (!asked || !area.width || handled.current === asked.nonce) return;
    handled.current = asked.nonce;
    const current = perform.current;
    const index = current.views.ordered.findIndex((view) => view.id === asked.viewId);
    const view = index === -1 ? null : current.views.ordered[index];
    // A window that has been closed since the ask is not an error; it is just
    // nothing to do. Clearing it either way keeps a stale ask from sitting
    // there and firing at the next window to take that id's place.
    if (view) current.putAway(view, index);
    current.views.clearWindowMotion(asked.nonce);
  }, [asked, area.width]);

  // Bringing a window forward, from its chrome or from inside the app it runs.
  const focusView = useCallback(
    (viewId) => {
      if (views.layout.selectedView !== viewId) views.select(viewId);
      views.patchView(viewId, { raise: true });
    },
    [views],
  );
  useFrameFocus(host, focusView);

  const requestClose = useCallback(
    async (view) => {
      // Close is the only one of the three controls that ends anything, so it
      // is the only one that asks.
      if (!dirty[view.id]?.dirty) {
        views.close(view.id);
        return;
      }
      const sure = await confirm({
        title: `Close ${labelFor(view, apps)}?`,
        message: 'It has unsaved work. Closing the window ends its session.',
        confirmText: 'Close anyway',
        cancelText: 'Keep it open',
      });
      if (sure) views.close(view.id);
    },
    [apps, confirm, dirty, views],
  );

  if (!views.loaded || !views.ordered.length) return null;

  const { layout } = views;
  // A split that is too narrow to show as two is still a split. It is drawn as
  // one pane at a time; the divider, the empty slots and the snap preview all
  // belong to the wide presentation and are not drawn here.
  const split = layout.arrangement === 'split';
  const wideEnough = area.width >= SPLIT_MIN_WIDTH;
  const ratio = ratioPreview ?? layout.dividerRatio ?? 0.5;
  const drawn = ratioPreview === null ? layout : { ...layout, dividerRatio: ratio };
  const travelling = motion.fallback;
  // In a stable order, each carrying the depth its stack gives it. Drawing
  // them in stack order would mean raising a window moved its element, which
  // reloads the iframe inside it — see `stackLayers`.
  const visible = stackLayers(views.ordered)
    .map(({ view, depth }) => {
      // A window with no picture of itself travels in person: the arrangement
      // has already put it away, so it is drawn where it was and moved by
      // transform. The frame inside it is never unmounted to do this.
      if (travelling?.viewId === view.id) {
        return { view, depth, bounds: travelling.bounds, travel: fallbackStyle(travelling) };
      }
      return {
        view,
        depth,
        bounds: placeView(view, {
          layout: drawn,
          area,
          index: depth,
          size: windowOptionsFor(view, apps).defaultSize || null,
        }),
      };
    })
    // The window the canvas overlay is standing in for is hidden while it
    // stands in for it, so the two are never both on screen. Its session is
    // untouched either way.
    .filter((entry) => entry.bounds && motion.animatingViewId !== entry.view.id);
  const openElsewhere = views.ordered
    .filter((view) => !view.window?.minimized)
    .map((view) => ({ id: view.id, label: labelFor(view, apps) }));

  const menuFor = (view) => {
    // A pane is a size, not a place: taking half the screen means being resized
    // to half the screen. An app that said its window is a fixed size is not
    // offered that, here or by dragging to an edge.
    const items =
      windowOptionsFor(view, apps).resizable === false
        ? []
        : [
            {
              label:
                split && layout.primaryView === view.id
                  ? 'Already on the left'
                  : 'Move to the left',
              disabled: split && layout.primaryView === view.id,
              onSelect: () => views.snap(view.id, 'left'),
            },
            {
              label:
                split && layout.secondaryView === view.id
                  ? 'Already on the right'
                  : 'Move to the right',
              disabled: split && layout.secondaryView === view.id,
              onSelect: () => views.snap(view.id, 'right'),
            },
          ];
    if (split) {
      items.push(
        { separator: true },
        { label: 'Swap the two panes', onSelect: () => views.swapPanes() },
        { label: 'Even them up', onSelect: () => views.setDivider(0.5) },
        { label: 'Leave split view', onSelect: () => views.exitSplit() },
      );
    }
    return items;
  };

  return (
    <div className="view-host" ref={host}>
      {visible.map(({ view, depth, bounds, travel }) => {
        const held = dragging?.id === view.id ? dragging.bounds : bounds;
        const maximized = layout.arrangement === 'maximized' && layout.maximizedView === view.id;
        // A window in flight is placed by the motion, not by the pointer.
        const placed = layout.arrangement !== 'floating' || Boolean(travel);
        const options = windowOptionsFor(view, apps);
        const name = labelFor(view, apps);
        return (
          <WindowFrame
            key={view.id}
            title={name}
            icon={iconFor(view, apps, name)}
            bounds={held}
            area={area}
            selected={layout.selectedView === view.id}
            maximized={maximized}
            depth={depth}
            placed={placed}
            resizable={options.resizable !== false}
            maximizable={options.maximizable !== false}
            travel={travel}
            status={view.available ? null : 'Needs reopening'}
            busy={Boolean(busy[view.id])}
            viewId={view.id}
            onSelect={() => focusView(view.id)}
            onMove={onMove(view.id)}
            onDragPoint={onDragPoint(view.id)}
            actions={menuFor(view)}
            onMinimize={() => putAway(view, depth)}
            onMaximize={() => views.maximize(view, { maximizable: options.maximizable !== false })}
            onClose={() => requestClose(view)}
          >
            {view.kind === 'app' ? (
              <AppWindow
                view={view}
                frameRef={frameFor(view.id)}
                onDirty={(state) => setDirty((previous) => ({ ...previous, [view.id]: state }))}
                onDisconnect={() => setDirty((previous) => ({ ...previous, [view.id]: null }))}
                onBusy={(state) => setBusy((previous) => ({ ...previous, [view.id]: state }))}
              />
            ) : view.kind === 'host' && view.surface === 'settings' ? (
              // Owner chrome in a window, like the agent window below: the
              // dashboard's own Settings, not a page loaded into a frame.
              <Settings
                windowed
                sectionRequest={sectionRequest}
                onClose={() => requestClose(view)}
              />
            ) : view.kind === 'agent' ? (
              // Owner chrome in a window, not an app. Nothing in the agent's
              // browser can see this or reach what it calls.
              <AgentWindow desktopId={view.desktopId} />
            ) : (
              <div className="window-state" role="status">
                <p>This kind of window is not available yet.</p>
              </div>
            )}
          </WindowFrame>
        );
      })}

      {/* The divider is drawn over the gutter the panes already leave for it, so
          it never covers what is inside either one. */}
      {split && wideEnough && area.width > 0 && (
        <SplitDivider
          area={area}
          ratio={layout.dividerRatio ?? 0.5}
          onPreview={setRatioPreview}
          onCommit={(next) => {
            setRatioPreview(null);
            views.setDivider(next);
          }}
        />
      )}

      {split &&
        wideEnough &&
        panes(drawn)
          .filter((pane) => !pane.viewId)
          .map((pane) => (
            <EmptyPane
              key={pane.side}
              side={pane.side}
              bounds={splitBounds(area, ratio, pane.side === 'left' ? 'primary' : 'secondary')}
              openViews={openElsewhere.filter(
                (entry) => entry.id !== layout.primaryView && entry.id !== layout.secondaryView,
              )}
              apps={apps}
              onChoose={(viewId) => views.snap(viewId, pane.side)}
              onOpenApp={async (appId) => {
                const view = await views.open({ kind: 'app', appId });
                if (view?.id) views.snap(view.id, pane.side);
              }}
              onExit={() => views.exitSplit()}
            />
          ))}

      {/* What a drop would do, shown before it happens. Decorative and never in
          the way: it cannot be clicked and it holds no focus. */}
      {snapping?.side && (
        <div
          className="snap-preview"
          aria-hidden="true"
          style={(() => {
            const box = previewBounds(snapping.side, area, layout.dividerRatio ?? 0.5);
            return {
              left: `${box.x}px`,
              top: `${box.y}px`,
              width: `${box.width}px`,
              height: `${box.height}px`,
            };
          })()}
        />
      )}

      {/* Above the window it replaces and below everything the owner needs.
          Decoration: it takes no pointer input and is not in the accessibility
          tree, so every real control stays exactly as reachable as it was. */}
      <GenieOverlay
        run={motion.run}
        area={area}
        onDone={motion.settle}
        onProgress={motion.progressed}
      />
    </div>
  );
}
