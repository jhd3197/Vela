import { useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowSquareOut,
  ArrowsClockwise,
  DotsThreeOutline,
  GearSix,
  PlusCircle,
  PushPin,
  PushPinSlash,
  Stop,
  X,
} from '@phosphor-icons/react';
import AppIcon from './AppIcon.jsx';
import NotificationBell from './NotificationBell.jsx';
import ContextMenu from './ui/ContextMenu.jsx';

// The state pill: one word about the app, coloured by what it means.
function statePill(state) {
  const labels = {
    running: 'Running',
    starting: 'Starting',
    stopped: 'Stopped',
    unsupported: 'Not supported',
    unavailable: 'Unavailable',
  };
  const label = labels[state];
  if (!label) return null;
  return <span className={`app-pill app-pill-${state}`}>{label}</span>;
}

// The app window's own title bar: a way back to where the app was opened from,
// its identity and state, a thin progress line while it loads, up to two of the
// app's own quick actions, the notifications bell, and a menu of the things you
// do to a window — pin it, put its widget on the desk, reach its settings,
// reload it, open it in a new tab, stop it, or close it. No search field; the
// palette is still `Ctrl+K`. `appearance: 'dark'` draws the bar in the dark
// tokens whatever the hub theme is, so a dark app is not topped by a light strip.
export default function AppTitleBar({
  app,
  state,
  loading,
  appearance = 'auto',
  quickActions = [],
  onBack,
  pinned = false,
  onPin,
  onUnpin,
  canAddWidget = false,
  onAddWidget,
  onAppSettings,
  onHideBar,
  onReload,
  onOpenNewTab,
  onStop,
  canStop = false,
  onClose,
}) {
  const [menu, setMenu] = useState(null);
  const menuButton = useRef(null);

  const items = [];
  items.push(
    pinned
      ? { label: 'Unpin from rail', icon: PushPinSlash, onSelect: onUnpin }
      : { label: 'Pin to rail', icon: PushPin, onSelect: onPin },
  );
  if (canAddWidget)
    items.push({ label: 'Add widget to desk', icon: PlusCircle, onSelect: onAddWidget });
  if (onAppSettings) items.push({ label: 'App settings', icon: GearSix, onSelect: onAppSettings });
  if (onHideBar) items.push({ label: 'Hide app bar', icon: X, onSelect: onHideBar });
  items.push({ separator: true });
  if (onReload) items.push({ label: 'Reload app', icon: ArrowsClockwise, onSelect: onReload });
  if (onOpenNewTab)
    items.push({ label: 'Open in new tab', icon: ArrowSquareOut, onSelect: onOpenNewTab });
  if (canStop) items.push({ label: 'Stop app', icon: Stop, onSelect: onStop });
  items.push({ label: 'Close', icon: X, onSelect: onClose });

  const dark = appearance === 'dark';
  return (
    <header
      className={`app-titlebar${dark ? ' app-titlebar-dark' : ''}`}
      data-theme={dark ? 'dark' : undefined}
    >
      <button type="button" className="app-titlebar-back" aria-label="Back" onClick={onBack}>
        <ArrowLeft size={18} aria-hidden="true" />
      </button>
      {app ? <AppIcon app={app} size={22} /> : null}
      <span className="app-titlebar-name">{app?.name || 'App'}</span>
      {statePill(state)}
      {loading ? <span className="app-titlebar-progress" aria-hidden="true" /> : null}
      <div className="app-titlebar-side">
        {quickActions.slice(0, 2).map((action) => (
          <button
            key={action.id}
            type="button"
            className="btn btn-small"
            disabled={action.disabled}
            onClick={action.onSelect}
          >
            {action.label}
          </button>
        ))}
        <NotificationBell />
        <button
          ref={menuButton}
          type="button"
          className="btn btn-icon"
          aria-label="App menu"
          aria-haspopup="menu"
          aria-expanded={Boolean(menu)}
          onClick={(event) => {
            const box = event.currentTarget.getBoundingClientRect();
            setMenu({ x: box.right - 200, y: box.bottom + 4 });
          }}
        >
          <DotsThreeOutline size={18} weight="fill" aria-hidden="true" />
        </button>
      </div>
      <ContextMenu
        open={Boolean(menu)}
        x={menu?.x || 0}
        y={menu?.y || 0}
        items={items}
        label={`${app?.name || 'App'} menu`}
        returnFocusRef={menuButton}
        onClose={() => setMenu(null)}
      />
    </header>
  );
}
