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
import WindowFrame from './WindowFrame.jsx';
import { placeView, workArea } from './window-state.js';

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

export default function DesktopViewHost({ views }) {
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
  const frames = useRef(new Map());

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
  const visible = views.ordered
    .map((view, index) => ({ view, bounds: placeView(view, { layout, area, index }) }))
    .filter((entry) => entry.bounds);

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
            onMinimize={() => views.minimize(view)}
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
