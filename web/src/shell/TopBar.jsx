// Vela's top bar: what has focus, what it can do, and what is going on.
//
// It reads like a menu bar on a Mac and it works like one. The left names
// whatever has focus — the selected window on the desk, or the page you are
// on — and carries that app's declared menus. The right is the rail of things
// that must be reachable from anywhere: items apps have published while they
// run, search, notifications, and the time.
//
// Two rules hold it together. **The bar is shell chrome, not page content**:
// it is rendered once, by `Shell`, above the workspace, and it does not remount
// when you navigate. **The bar draws; apps do not.** An app declares menu
// labels in its manifest and publishes status items as data; every pixel here
// is a host component, and nothing an app sends is ever interpreted as markup,
// as a URL or as a colour.
//
// It is not rendered at phone widths. There the rail is the only chrome, a
// second strip would take a line of screen that is already short, and every
// control here is still reachable from the workspace header.
import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  BatteryHigh,
  Bell,
  CalendarBlank,
  Check,
  CloudSun,
  Clock,
  Database,
  DownloadSimple,
  Envelope,
  Globe,
  Heart,
  LockSimple,
  MoonStars,
  MusicNote,
  Pulse,
  Sun,
  Thermometer,
  UploadSimple,
  WarningCircle,
  WifiHigh,
} from '@phosphor-icons/react';
import AppIcon from '../components/AppIcon.jsx';
import GlobalSearch from '../components/GlobalSearch.jsx';
import NotificationBell from '../components/NotificationBell.jsx';
import ContextMenu from '../components/ui/ContextMenu.jsx';
import { useDesktops } from '../desktops/DesktopsProvider.jsx';
import { coreById, dashboardPages } from '../navigation.js';
import { useApps } from '../store.jsx';
import { resolveFocus } from './focus.js';
import { MENU_ACTIONS } from './topbar-contract.js';
import { useTopBarItems } from './TopBarProvider.jsx';

// The vetted icon set, as components. An app names a key; it can never reach
// past this map, which is the difference between "choose an icon" and "point
// the bar at an image". Keep it in step with `STATUS_ICONS` in
// `topbar-contract.js` and with the list in `docs/CONTRACT.md`.
const ICONS = {
  battery: BatteryHigh,
  bell: Bell,
  calendar: CalendarBlank,
  check: Check,
  clock: Clock,
  cloud: CloudSun,
  database: Database,
  download: DownloadSimple,
  envelope: Envelope,
  globe: Globe,
  heart: Heart,
  lock: LockSimple,
  moon: MoonStars,
  music: MusicNote,
  pulse: Pulse,
  sun: Sun,
  thermometer: Thermometer,
  upload: UploadSimple,
  warning: WarningCircle,
  wifi: WifiHigh,
};

/** The clock, to the minute. A second hand in a menu bar is a distraction. */
function useMinute() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    // Line up with the next minute rather than ticking every sixty seconds
    // from whenever the dashboard happened to load, so the displayed time
    // changes when the minute does.
    let timer = null;
    const schedule = () => {
      const delay = 60000 - (Date.now() % 60000);
      timer = setTimeout(() => {
        setNow(new Date());
        schedule();
      }, delay);
    };
    schedule();
    return () => clearTimeout(timer);
  }, []);
  return now;
}

/** One published item: a mark, a word, or both, and a tooltip. */
function StatusItem({ item, onOpen }) {
  const Icon = item.icon ? ICONS[item.icon] : null;
  const tone = item.tone && item.tone !== 'neutral' ? ` is-${item.tone}` : '';
  return (
    <button
      type="button"
      className={`topbar-status${tone}`}
      title={item.title || undefined}
      aria-label={item.title || item.label || item.appId}
      onClick={() => onOpen(item)}
    >
      {Icon ? <Icon size={15} aria-hidden="true" /> : null}
      {item.label ? <span className="topbar-status-label">{item.label}</span> : null}
    </button>
  );
}

export default function TopBar() {
  const location = useLocation();
  const navigate = useNavigate();
  const { apps } = useApps();
  const { views } = useDesktops();
  const { items } = useTopBarItems();
  const now = useMinute();
  // Which menu is open, and where to draw it. One at a time: a menu bar with
  // two menus down is a menu bar that has lost track of itself.
  const [menu, setMenu] = useState(null);
  const menuButtons = useRef(new Map());

  // The page table is passed in rather than imported by the resolver: that
  // module stays free of every page component so its rules can be checked
  // without a browser.
  const focus = useMemo(
    () => resolveFocus({ pathname: location.pathname, views, apps, pages: dashboardPages }),
    [location.pathname, views, apps],
  );

  // Close whatever is open when focus moves: the menus belonged to the app
  // that had focus a moment ago, and leaving one down over a different app's
  // name is how somebody chooses the wrong thing.
  useEffect(() => setMenu(null), [focus.appId, focus.name]);

  const app = focus.appId ? apps?.find((entry) => entry.id === focus.appId) : null;
  const menus = app?.topbarMenus || [];

  // The tile beside the name: the app's own icon, a core tool's mark, or
  // nothing at all for the desk, which is not a thing with an icon.
  const core = focus.kind === 'page' && focus.id ? coreById(focus.id) : null;
  const identity = app
    ? app
    : core
      ? { id: core.id, name: core.label, glyph: core.icon, color: core.color }
      : null;

  /** What a menu item's action does. Two verbs, both of them the host's own. */
  const perform = (action) => {
    setMenu(null);
    if (!MENU_ACTIONS.includes(action)) return;
    if (action === 'close' && focus.viewId) {
      views.close(focus.viewId);
      return;
    }
    // `close` on an app's own page, and `return` anywhere, mean the same
    // thing: leave the app and go back to the desk. The window, if there is
    // one, stays open — returning is not closing.
    navigate('/');
  };

  const openMenu = (id) => {
    if (menu === id) {
      setMenu(null);
      return;
    }
    setMenu(id);
  };

  const raise = (item) => {
    // The only thing a published item does. Not a link, not an action: the app
    // asked for attention and this is where attention goes.
    if (!item.viewId) return;
    navigate('/');
    views.select(item.viewId);
    views.patchView(item.viewId, { minimized: false, raise: true }, { immediate: true });
  };

  const open = menus.find((entry) => entry.id === menu) || null;
  const anchor = menu ? menuButtons.current.get(menu) : null;
  const box = anchor?.getBoundingClientRect();

  return (
    <header className="topbar">
      <div className="topbar-lead">
        {identity ? <AppIcon app={identity} size={18} /> : null}
        <span className="topbar-name">{focus.name}</span>
        {menus.length ? (
          <nav className="topbar-menus" aria-label={`${focus.name} menus`}>
            {menus.map((entry) => (
              <button
                key={entry.id}
                type="button"
                className={`topbar-menu${menu === entry.id ? ' is-open' : ''}`}
                aria-haspopup="menu"
                aria-expanded={menu === entry.id}
                ref={(node) => {
                  if (node) menuButtons.current.set(entry.id, node);
                  else menuButtons.current.delete(entry.id);
                }}
                onClick={() => openMenu(entry.id)}
              >
                {entry.label}
              </button>
            ))}
          </nav>
        ) : null}
      </div>

      <div className="topbar-rail">
        {items.length ? (
          <div className="topbar-statuses">
            {items.map((item) => (
              <StatusItem key={`${item.viewId}:${item.id}`} item={item} onOpen={raise} />
            ))}
          </div>
        ) : null}
        <GlobalSearch compact />
        <NotificationBell />
        <time className="topbar-clock" dateTime={now.toISOString()}>
          {now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}
        </time>
      </div>

      {open && box ? (
        <ContextMenu
          open
          x={box.left}
          y={box.bottom + 4}
          label={`${open.label} menu`}
          // An item whose action this host does not perform is still drawn —
          // the app declared it and hiding it would make the menu lie about
          // what the app offers — but it is inert rather than a button that
          // silently does nothing when clicked.
          items={open.items.map((item) => ({
            label: item.label,
            disabled: !MENU_ACTIONS.includes(item.action),
            onSelect: () => perform(item.action),
          }))}
          onClose={() => setMenu(null)}
        />
      ) : null}
    </header>
  );
}
