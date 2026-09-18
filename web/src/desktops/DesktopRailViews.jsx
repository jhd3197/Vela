// The windows open on this desktop, in the rail.
//
// The rail is the open-window navigator. There is deliberately no second
// horizontal strip of tabs: one place that answers "what is open and which one
// am I looking at" is easier to trust than two that can disagree.
//
// Four states, and none of them is inferred from another: selected, open,
// minimized, and needs reopening because its installation was replaced. A
// minimized window is still open — that is the whole point of minimizing — so
// it keeps its entry and says so.
import { useMemo } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import AppIcon from '../components/AppIcon.jsx';
import { useApps } from '../store.jsx';
import { useDesktops } from './DesktopsProvider.jsx';

function label(view, apps) {
  if (view.title) return view.title;
  if (view.kind === 'app') return apps?.find((app) => app.id === view.appId)?.name || 'App';
  if (view.kind === 'host') return view.surface === 'library' ? 'Marketplace' : 'Ask';
  if (view.kind === 'web') {
    try {
      return new URL(view.url).hostname;
    } catch {
      return 'Web';
    }
  }
  return 'Agent';
}

export default function DesktopRailViews({ onNavigate }) {
  const { apps } = useApps();
  const { views } = useDesktops();
  const navigate = useNavigate();
  const location = useLocation();
  // In the order they were opened, not the order they are stacked: a shortcut
  // that moved every time a window came forward would be a shortcut nobody
  // could reach for without looking.
  const open = useMemo(
    () => (views.views || []).slice().sort((a, b) => a.position - b.position),
    [views.views],
  );

  if (!open.length) return null;

  return (
    <div className="rail-apps-group rail-views" aria-label="Open windows" role="group">
      <span className="rail-section-label" aria-hidden="true">
        OPEN
      </span>
      {open.map((view) => {
        const name = label(view, apps);
        const selected = views.layout?.selectedView === view.id;
        const minimized = view.window?.minimized;
        return (
          <button
            key={view.id}
            type="button"
            className={`rail-item rail-view-item${selected ? ' rail-item-active' : ''}`}
            // The window motion measures this to know where to fly. Measured
            // every time rather than remembered: the rail scrolls, the theme
            // changes its size, and a hardcoded coordinate would eventually be
            // an animation into the edge of the screen.
            data-motion-anchor={view.id}
            data-state={minimized ? 'minimized' : 'open'}
            aria-pressed={selected}
            onClick={() => {
              onNavigate?.();
              // Windows are drawn on the desk, so reaching for one means going
              // there. The ask below survives that navigation because it is
              // recorded as state rather than fired as an event.
              if (location.pathname !== '/') navigate('/');
              // The three things a taskbar button means, and they stay three
              // different things. A window that is away comes back; the one
              // you are already in goes away; any other comes forward. None of
              // them starts anything or ends anything — the app keeps running
              // whichever it is.
              if (minimized || selected) views.toggleWindow(view.id);
              else views.patchView(view.id, { raise: true });
              if (!selected) views.select(view.id);
            }}
          >
            {view.kind === 'app' ? (
              <AppIcon
                app={apps?.find((app) => app.id === view.appId) || { id: view.appId, name }}
                size={34}
              />
            ) : (
              <span className="rail-view-mark" aria-hidden="true">
                {name.slice(0, 1)}
              </span>
            )}
            <span className="rail-tip">
              {name}
              {minimized ? ' — minimized' : ''}
              {view.available ? '' : ' — needs reopening'}
            </span>
            <span className="sr-only">
              {name}
              {minimized ? ', minimized' : ''}
              {selected ? ', selected' : ''}
              {view.available ? '' : ', needs reopening'}
            </span>
            {!view.available ? <span className="rail-dot" aria-hidden="true" /> : null}
          </button>
        );
      })}
    </div>
  );
}
