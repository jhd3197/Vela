import { useEffect, useMemo, useRef, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { DotsThreeOutline, SignOut } from '@phosphor-icons/react';
import { railGroup } from '../navigation.js';
import { useApps } from '../store.jsx';
import { useDeveloperTools } from '../developer.js';
import { useAuth } from './AuthGate.jsx';
import { useSettingsPopup } from './SettingsProvider.jsx';
import AppIcon from './AppIcon.jsx';

// Installed apps keep a stable, status-independent order so a shortcut never
// moves while the user is reaching for it.
export function railApps(apps) {
  return (apps || [])
    .filter((app) => app.installed)
    .slice()
    .sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
}

function RailLink({ to, end, label, badge, onNavigate, children }) {
  return (
    <NavLink
      to={to}
      end={end}
      onClick={onNavigate}
      className={({ isActive }) => `rail-item${isActive ? ' rail-item-active' : ''}`}
    >
      {children}
      <span className="rail-tip">{label}</span>
      {badge ? (
        <span className="rail-badge" aria-hidden="true">
          {badge}
        </span>
      ) : null}
    </NavLink>
  );
}

// The secondary destinations: management and automation, named in full rather
// than hidden behind another icon. Escape and a click outside return focus to
// the opener.
function MoreMenu({ pages, onNavigate }) {
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const wrap = useRef(null);
  const button = useRef(null);
  const active = pages.some((page) => location.pathname.startsWith(page.to));

  useEffect(() => {
    if (!open) return undefined;
    wrap.current?.querySelector('.rail-menu a')?.focus();
    const dismiss = (event) => {
      if (event.type === 'keydown' && event.key !== 'Escape') return;
      if (event.type === 'pointerdown' && wrap.current?.contains(event.target)) return;
      setOpen(false);
      if (event.type === 'keydown') button.current?.focus();
    };
    addEventListener('keydown', dismiss);
    addEventListener('pointerdown', dismiss);
    return () => {
      removeEventListener('keydown', dismiss);
      removeEventListener('pointerdown', dismiss);
    };
  }, [open]);

  if (pages.length === 0) return null;

  return (
    <div className="rail-more" ref={wrap}>
      <button
        ref={button}
        type="button"
        className={`rail-item${active ? ' rail-item-active' : ''}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <DotsThreeOutline size={19} weight="fill" aria-hidden="true" />
        <span className="rail-tip">More</span>
      </button>
      {open && (
        <div className="rail-menu" role="menu" aria-label="More">
          {pages.map(({ to, label, icon: Icon, weight = 'regular' }) => (
            <NavLink
              key={to}
              to={to}
              role="menuitem"
              className={({ isActive }) => `rail-menu-item${isActive ? ' is-active' : ''}`}
              onClick={() => {
                setOpen(false);
                onNavigate?.();
              }}
            >
              <Icon size={17} weight={weight} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </div>
      )}
    </div>
  );
}

// The Vela rail: global destinations, real installed-app shortcuts and the
// account controls. It is rendered once beside the workspace, and again inside
// the phone drawer, which passes `onNavigate` so choosing a destination closes
// it. Home keeps this rail on screen at every width.
export default function AppRail({ onNavigate }) {
  const { apps } = useApps();
  const { remote, logout } = useAuth();
  const { openSettings } = useSettingsPopup();
  const developer = useDeveloperTools();
  const installed = useMemo(() => railApps(apps), [apps]);
  const availableCount = (apps || []).filter((a) => !a.installed && a.supported).length;

  return (
    <nav className="rail" aria-label="Vela">
      <img className="rail-mark" src="/vela-mark.png" alt="" aria-hidden="true" />

      <div className="rail-group">
        {railGroup('primary', developer).map(({ to, end, label, icon: Icon }) => (
          <RailLink key={to} to={to} end={end} label={label} onNavigate={onNavigate}>
            <Icon size={20} weight="fill" aria-hidden="true" />
          </RailLink>
        ))}
      </div>

      {installed.length > 0 && (
        <div className="rail-apps" aria-label="Installed apps" role="group">
          {installed.map((app) => (
            <RailLink key={app.id} to={`/app/${app.id}`} label={app.name} onNavigate={onNavigate}>
              <AppIcon app={app} size={38} />
            </RailLink>
          ))}
        </div>
      )}

      <span className="rail-divider" aria-hidden="true" />

      <div className="rail-group rail-group-tools">
        {railGroup('tools', developer).map(({ to, label, icon: Icon, weight = 'regular' }) => (
          <RailLink
            key={to}
            to={to}
            label={label}
            badge={to === '/library' && availableCount > 0 ? availableCount : null}
            onNavigate={onNavigate}
          >
            <Icon size={19} weight={weight} aria-hidden="true" />
          </RailLink>
        ))}
        <MoreMenu pages={railGroup('more', developer)} onNavigate={onNavigate} />
      </div>

      <div className="rail-foot">
        {railGroup('foot', developer).map(({ to, label, icon: Icon }) => (
          <button
            key={to}
            type="button"
            className="rail-item"
            aria-haspopup="dialog"
            onClick={() => {
              onNavigate?.();
              openSettings();
            }}
          >
            <Icon size={19} aria-hidden="true" />
            <span className="rail-tip">{label}</span>
          </button>
        ))}
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
    </nav>
  );
}
