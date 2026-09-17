import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import {
  ArrowLineDown,
  ArrowLineUp,
  PushPin,
  PushPinSlash,
  SignOut,
  SquaresFour,
} from '@phosphor-icons/react';
import { coreApps, coreById, railGroup } from '../navigation.js';
import { useApps } from '../store.jsx';
import { api } from '../api.js';
import { useResource } from '../hooks/useResource.js';
import { useDeveloperTools } from '../developer.js';
import useLongPress from '../hooks/useLongPress.js';
import { useAuth } from './AuthGate.jsx';
import { useSettingsPopup } from './SettingsProvider.jsx';
import AppIcon from './AppIcon.jsx';
import DesktopSwitcher from '../desktops/DesktopSwitcher.jsx';
import { useAppsOverlay } from '../desktops/AppsOverlay.jsx';
import ContextMenu from './ui/ContextMenu.jsx';

// Installed apps in a stable, status-independent order so a shortcut never
// moves while the user is reaching for it.
export function railApps(apps) {
  return (apps || [])
    .filter((app) => app.installed)
    .slice()
    .sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
}

// A core app drawn as a rail tile: the same tinted tile an installed app gets,
// with the tool's own Phosphor mark inside it.
function coreTile(entry) {
  return { id: entry.id, name: entry.label, glyph: entry.icon, color: entry.color };
}

// One rail item, whether it links to a route, opens an app, or opens the
// settings popup. It carries the tooltip, an optional badge and attention dot,
// and forwards the right-click / long-press that opens its context menu.
function RailItem({
  to,
  end,
  label,
  onActivate,
  onContextMenu,
  longPress,
  className = '',
  badge,
  attention,
  children,
}) {
  const shared = {
    className: ({ isActive } = {}) =>
      `rail-item${isActive ? ' rail-item-active' : ''}${className ? ` ${className}` : ''}`,
    onContextMenu,
    ...longPress,
  };
  const body = (
    <>
      {children}
      <span className="rail-tip">{label}</span>
      {badge ? (
        <span className="rail-badge" aria-hidden="true">
          {badge}
        </span>
      ) : null}
      {attention ? <span className="rail-dot" aria-hidden="true" /> : null}
    </>
  );
  if (to) {
    return (
      <NavLink to={to} end={end} {...shared}>
        {body}
      </NavLink>
    );
  }
  return (
    <button
      type="button"
      className={shared.className()}
      onClick={onActivate}
      {...longPress}
      onContextMenu={onContextMenu}
    >
      {body}
    </button>
  );
}

// The Vela rail: Desk and the Launchpad fixed at the top, then the apps the
// user pinned (core tools and installed apps alike, in their saved order),
// a divider, the apps that are open but not pinned, and the account controls.
// It is rendered once beside the workspace, and again inside the phone drawer,
// which passes `onNavigate` so choosing a destination closes it.
export default function AppRail({ onNavigate }) {
  const { apps, openApp, pinned, pinApp, unpinApp, movePin } = useApps();
  const { remote, logout } = useAuth();
  const { openSettings } = useSettingsPopup();
  const { appsOpen, openApps } = useAppsOverlay();
  const developer = useDeveloperTools();
  const location = useLocation();
  const availableCount = (apps || []).filter((a) => !a.installed && a.supported).length;

  const [menu, setMenu] = useState(null); // { kind, id, x, y }
  const menuOpener = useRef(null);

  const loadSummaries = useCallback((options) => api.appWidgets(options), []);
  const { data, refresh: refreshSummaries } = useResource(loadSummaries, { intervalMs: 60000 });
  const summaries = data?.widgets;
  // Putting an item aside on the desk should take this dot with it, not leave
  // it on until the next minute's poll.
  useEffect(() => {
    const onChanged = () => refreshSummaries();
    addEventListener('vela:widgets-changed', onChanged);
    return () => removeEventListener('vela:widgets-changed', onChanged);
  }, [refreshSummaries]);
  // Settings holds Health, so a failed check is what puts a dot on Settings.
  // Reading the last sweep never starts one.
  const loadHealth = useCallback((options) => api.getDoctor(options), []);
  const { data: health } = useResource(loadHealth, { intervalMs: 120000 });
  // Who this server belongs to. The avatar is the one place the rail says it,
  // so it reads the setting rather than being handed a prop through the shell.
  const loadIdentity = useCallback((options) => api.getSettings(options), []);
  const { data: settingsData, refresh: refreshIdentity } = useResource(loadIdentity);
  const identity = settingsData?.identity || {};
  useEffect(() => {
    const onChanged = () => refreshIdentity();
    addEventListener('vela:identity-changed', onChanged);
    return () => removeEventListener('vela:identity-changed', onChanged);
  }, [refreshIdentity]);
  const healthFailing = (health?.checks || []).some((check) => check.status === 'fail');
  const needsAttention = useMemo(() => {
    const ids = new Set();
    for (const entry of summaries || []) {
      // A snoozed item is put aside on the desk, so the dot goes with it.
      if (entry?.summary?.attention && !entry.snoozedUntil) ids.add(entry.appId);
    }
    return ids;
  }, [summaries]);

  const installed = useMemo(() => railApps(apps), [apps]);
  const byId = useMemo(() => new Map(installed.map((app) => [app.id, app])), [installed]);
  const coreIds = useMemo(() => new Set(coreApps(developer).map((entry) => entry.id)), [developer]);

  // Resolve each pinned id to something the rail can draw. Ids that no longer
  // name a core tool or an installed app are dropped, not shown as a blank.
  const pins = useMemo(() => {
    const resolved = [];
    for (const id of pinned) {
      if (coreIds.has(id)) {
        const entry = coreById(id);
        if (entry) resolved.push({ kind: 'core', id, entry });
      } else if (byId.has(id)) {
        resolved.push({ kind: 'app', id, app: byId.get(id) });
      }
    }
    return resolved;
  }, [pinned, coreIds, byId]);

  const pinnedSet = useMemo(() => new Set(pins.map((pin) => pin.id)), [pins]);
  // Apps that are open right now but not pinned; the pinned ones already show
  // above, so an open pinned app does not appear twice.
  const openUnpinned = useMemo(
    () => installed.filter((app) => app.running && !pinnedSet.has(app.id)),
    [installed, pinnedSet],
  );

  const openMenu = useCallback((next, event) => {
    event.preventDefault();
    menuOpener.current = event.currentTarget;
    setMenu({ ...next, x: event.clientX || 0, y: event.clientY || 0 });
  }, []);

  const openFrom = useCallback(
    (id) => openApp(id, { returnTo: location.pathname }),
    [openApp, location.pathname],
  );

  const menuItems = useMemo(() => {
    if (!menu) return [];
    if (menu.kind === 'pinned') {
      const index = pins.findIndex((pin) => pin.id === menu.id);
      const pin = pins[index];
      const items = [];
      if (pin?.kind === 'app') {
        items.push({ label: 'Open', icon: SquaresFour, onSelect: () => openFrom(menu.id) });
      }
      items.push({
        label: 'Move up',
        icon: ArrowLineUp,
        disabled: index <= 0,
        onSelect: () => movePin(menu.id, 'up'),
      });
      items.push({
        label: 'Move down',
        icon: ArrowLineDown,
        disabled: index < 0 || index >= pins.length - 1,
        onSelect: () => movePin(menu.id, 'down'),
      });
      items.push({ separator: true });
      items.push({
        label: 'Unpin from rail',
        icon: PushPinSlash,
        onSelect: () => unpinApp(menu.id),
      });
      return items;
    }
    // An open, unpinned app: open it or pin it.
    return [
      { label: 'Open', icon: SquaresFour, onSelect: () => openFrom(menu.id) },
      { label: 'Pin to rail', icon: PushPin, onSelect: () => pinApp(menu.id) },
    ];
  }, [menu, pins, movePin, unpinApp, pinApp, openFrom]);

  return (
    <nav className="rail" aria-label="Vela">
      <img className="rail-mark" src="/vela-mark.png" alt="" aria-hidden="true" />

      <div className="rail-group">
        {railGroup('primary', developer).map(({ to, end, label, overlay, icon: Icon }) =>
          // All apps opens over the page rather than replacing it, so its rail
          // entry asks the overlay to open instead of navigating away.
          overlay ? (
            <RailItem
              key={to}
              label={label}
              className={appsOpen ? 'rail-item-active' : ''}
              onActivate={() => {
                onNavigate?.();
                openApps();
              }}
              longPress={{}}
            >
              <Icon size={20} weight="fill" aria-hidden="true" />
            </RailItem>
          ) : (
            <RailItem
              key={to}
              to={to}
              end={end}
              label={label}
              onActivate={onNavigate}
              longPress={{}}
            >
              <Icon size={20} weight="fill" aria-hidden="true" />
            </RailItem>
          ),
        )}
      </div>

      {/* Which workspace this is. In the rail because the rail is the one piece
          of chrome that is there at every width and under every layout. */}
      <DesktopSwitcher onNavigate={onNavigate} />

      {(pins.length > 0 || openUnpinned.length > 0) && (
        <div className="rail-apps">
          {pins.length > 0 && (
            <div className="rail-apps-group" aria-label="Pinned apps" role="group">
              {pins.map((pin) =>
                pin.kind === 'core' ? (
                  <CorePin
                    key={pin.id}
                    entry={pin.entry}
                    badge={pin.id === 'library' && availableCount > 0 ? availableCount : null}
                    onNavigate={onNavigate}
                    openSettings={openSettings}
                    onContextMenu={(event) => openMenu({ kind: 'pinned', id: pin.id }, event)}
                  />
                ) : (
                  <RailItem
                    key={pin.id}
                    to={`/app/${pin.id}`}
                    label={pin.app.name}
                    attention={needsAttention.has(pin.id)}
                    onActivate={onNavigate}
                    longPress={{}}
                    onContextMenu={(event) => openMenu({ kind: 'pinned', id: pin.id }, event)}
                  >
                    <AppIcon app={pin.app} size={38} />
                  </RailItem>
                ),
              )}
            </div>
          )}

          {openUnpinned.length > 0 && (
            <div className="rail-apps-group rail-apps-open" aria-label="Open apps" role="group">
              <span className="rail-section-label" aria-hidden="true">
                OPEN
              </span>
              {openUnpinned.map((app) => (
                <RailItem
                  key={app.id}
                  to={`/app/${app.id}`}
                  label={app.name}
                  className="rail-item-open"
                  attention={needsAttention.has(app.id)}
                  onActivate={onNavigate}
                  longPress={{}}
                  onContextMenu={(event) => openMenu({ kind: 'open', id: app.id }, event)}
                >
                  <AppIcon app={app} size={38} />
                </RailItem>
              ))}
            </div>
          )}
        </div>
      )}

      <span className="rail-divider" aria-hidden="true" />

      <div className="rail-foot">
        {railGroup('foot', developer).map(({ to, label, icon: Icon }) => (
          <button
            key={to}
            type="button"
            className="rail-item"
            aria-haspopup="dialog"
            aria-describedby={healthFailing ? 'rail-health-attention' : undefined}
            onClick={() => {
              onNavigate?.();
              openSettings();
            }}
          >
            <Icon size={19} aria-hidden="true" />
            {healthFailing ? <span className="rail-dot" aria-hidden="true" /> : null}
            <span className="rail-tip">{label}</span>
            {healthFailing ? (
              <span id="rail-health-attention" hidden>
                Something needs your attention in Health
              </span>
            ) : null}
          </button>
        ))}
        {/* The foot avatar: who this is. It opens the same Settings popup the
            gear does, on General, where the two names are edited. Without a
            name there is nothing true to draw, so it is not drawn. */}
        {identity.initial ? (
          <button
            type="button"
            className="rail-item rail-avatar-item"
            aria-haspopup="dialog"
            aria-label={`${identity.displayName || 'You'}${
              identity.serverName ? ` · ${identity.serverName}` : ''
            } — open General settings`}
            onClick={() => {
              onNavigate?.();
              openSettings('general');
            }}
          >
            <span className="rail-avatar" aria-hidden="true">
              {identity.initial}
            </span>
            <span className="rail-tip">
              {identity.displayName || 'You'}
              {identity.serverName ? ` · ${identity.serverName}` : ''}
            </span>
          </button>
        ) : null}
        {remote && (
          <button
            type="button"
            className="rail-item"
            onClick={() => {
              onNavigate?.();
              logout();
            }}
          >
            <SignOut size={19} aria-hidden="true" />
            <span className="rail-tip">Sign out</span>
          </button>
        )}
      </div>

      <ContextMenu
        open={Boolean(menu)}
        x={menu?.x || 0}
        y={menu?.y || 0}
        items={menuItems}
        label="Rail actions"
        returnFocusRef={menuOpener}
        onClose={() => setMenu(null)}
      />
    </nav>
  );
}

// A pinned core tool. Settings opens its popup; every other tool is a route.
function CorePin({ entry, badge, onNavigate, openSettings, onContextMenu }) {
  const longPress = useLongPress(({ target }) => {
    // Long-press mirrors right-click: raise the same menu at the tile.
    const box = target.getBoundingClientRect();
    onContextMenu({
      preventDefault() {},
      currentTarget: target,
      clientX: box.right,
      clientY: box.top,
    });
  });
  const icon = <AppIcon app={coreTile(entry)} size={38} />;
  if (entry.popup) {
    return (
      <button
        type="button"
        className="rail-item"
        aria-haspopup="dialog"
        onContextMenu={onContextMenu}
        onClick={() => {
          onNavigate?.();
          openSettings();
        }}
        {...longPress}
      >
        {icon}
        <span className="rail-tip">{entry.label}</span>
      </button>
    );
  }
  return (
    <RailItem
      to={entry.to}
      label={entry.label}
      badge={badge}
      onActivate={onNavigate}
      longPress={longPress}
      onContextMenu={onContextMenu}
    >
      {icon}
    </RailItem>
  );
}
