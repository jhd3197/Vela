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
import Button from '../components/ui/Button.jsx';
import Dialog from '../components/ui/Dialog.jsx';
import { useApps } from '../store.jsx';
import AgentWindow from './AgentWindow.jsx';
import AppWindow from './AppWindow.jsx';
import EmptyPane from './EmptyPane.jsx';
import SplitDivider from './SplitDivider.jsx';
import WindowFrame from './WindowFrame.jsx';
import GenieOverlay from './motion/GenieOverlay.jsx';
import useWindowMotion from './motion/useWindowMotion.js';
import { anchorFor } from './motion/anchors.js';
import { panes, previewBounds, snapTargetFor } from './snap.js';
import { SPLIT_MIN_WIDTH, placeView, splitBounds, workArea } from './window-state.js';

/** What to call a view that has not been given a title. */
function labelFor(view, apps) {
  if (view.title) return view.title;
  if (view.kind === 'app') {
    return apps?.find((app) => app.id === view.appId)?.name || 'App';
  }
  if (view.kind === 'host') return view.surface === 'library' ? 'Marketplace' : 'Ask';
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

export default function DesktopViewHost({ views, desktop }) {
  const { apps } = useApps();
  const host = useRef(null);
  const [area, setArea] = useState({ width: 0, height: 0 });
  // A window being dragged is drawn from this rather than from the server copy,
  // so the pointer is followed at the refresh rate and the write happens once.
  const [dragging, setDragging] = useState(null);
  // Unsaved work lives inside the app's frame; the app tells the host about it
  // and the host is what asks before closing.
  const [dirty, setDirty] = useState({});
  const [confirm, setConfirm] = useState(null);
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
          setSnapping({ id: viewId, side: snapTargetFor(point, area) });
          return;
        }
        const offered = snapping?.id === viewId ? snapping.side : null;
        setSnapping(null);
        if (offered && options.drop) views.snap(viewId, offered);
      },
    [area, snapping, views],
  );

  // Put a window away, or bring it back, with the warp where one is possible.
  // The layout change happens first and unconditionally — somebody asked for
  // the window to be minimized, so it is minimized — and the picture follows.
  const putAway = useCallback(
    (view, index) => {
      const minimized = Boolean(view.window?.minimized);
      const bounds = placeView(view, { layout: views.layout, area, index });
      motion.animate(view, minimized ? 'expand' : 'collapse', {
        icon: anchorFor(view.id, host.current),
        bounds,
        apply: () => (minimized ? views.restore(view, index) : views.minimize(view)),
      });
    },
    [area, motion, views],
  );

  const requestClose = useCallback(
    (view) => {
      // Close is the only one of the three controls that ends anything, so it
      // is the only one that asks.
      if (dirty[view.id]?.dirty) setConfirm(view);
      else views.close(view.id);
    },
    [dirty, views],
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
  const visible = views.ordered
    .map((view, index) => ({ view, bounds: placeView(view, { layout: drawn, area, index }) }))
    // The window the overlay is standing in for is hidden while it stands in
    // for it, so the two are never both on screen. Its session is untouched.
    .filter((entry) => entry.bounds && motion.animatingViewId !== entry.view.id);
  const openElsewhere = views.ordered
    .filter((view) => !view.window?.minimized)
    .map((view) => ({ id: view.id, label: labelFor(view, apps) }));

  const menuFor = (view) => {
    const items = [
      {
        label: split && layout.primaryView === view.id ? 'Already on the left' : 'Move to the left',
        disabled: split && layout.primaryView === view.id,
        onSelect: () => views.snap(view.id, 'left'),
      },
      {
        label:
          split && layout.secondaryView === view.id ? 'Already on the right' : 'Move to the right',
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
      {visible.map(({ view, bounds }) => {
        const held = dragging?.id === view.id ? dragging.bounds : bounds;
        const maximized = layout.arrangement === 'maximized' && layout.maximizedView === view.id;
        const fixed = layout.arrangement !== 'floating';
        const name = labelFor(view, apps);
        return (
          <WindowFrame
            key={view.id}
            title={name}
            icon={
              view.kind === 'app' ? (
                <AppIcon
                  app={apps?.find((app) => app.id === view.appId) || { id: view.appId, name }}
                  size={20}
                />
              ) : null
            }
            bounds={held}
            area={area}
            selected={layout.selectedView === view.id}
            maximized={maximized}
            fixed={fixed}
            status={view.available ? null : 'Needs reopening'}
            onSelect={() => {
              if (layout.selectedView !== view.id) views.select(view.id);
              views.patchView(view.id, { raise: true });
            }}
            onMove={onMove(view.id)}
            onDragPoint={onDragPoint(view.id)}
            actions={menuFor(view)}
            onMinimize={() => putAway(view, views.ordered.indexOf(view))}
            onMaximize={() => views.maximize(view)}
            onClose={() => requestClose(view)}
          >
            {view.kind === 'app' ? (
              <AppWindow
                view={view}
                frameRef={frameFor(view.id)}
                onDirty={(state) => setDirty((previous) => ({ ...previous, [view.id]: state }))}
                onDisconnect={() => setDirty((previous) => ({ ...previous, [view.id]: null }))}
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

      {confirm && (
        <Dialog open aria-labelledby="window-unsaved-title" onClose={() => setConfirm(null)}>
          <h2 id="window-unsaved-title">Close {labelFor(confirm, apps)}?</h2>
          <p>It has unsaved work. Closing the window ends its session.</p>
          <div className="form-actions">
            <Button
              variant="danger"
              onClick={() => {
                const view = confirm;
                setConfirm(null);
                views.close(view.id);
              }}
            >
              Close anyway
            </Button>
            <Button variant="ghost" onClick={() => setConfirm(null)}>
              Keep it open
            </Button>
          </div>
        </Dialog>
      )}
    </div>
  );
}
