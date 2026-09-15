import { useRef } from 'react';
import { NavLink } from 'react-router-dom';
import { SignOut } from '@phosphor-icons/react';
import { visiblePages } from '../navigation.js';
import { useApps } from '../store.jsx';
import { useDeveloperTools } from '../developer.js';
import { useAuth } from './AuthGate.jsx';
import { useSettingsPopup } from './SettingsProvider.jsx';
import AppIcon from './AppIcon.jsx';
import Drawer from './ui/Drawer.jsx';
import AppRail, { railApps } from './AppRail.jsx';

// The labelled list beside the rail: every destination and installed app with
// its name, for pages that bring no panel of their own.
function NavList({ onClose, firstRef }) {
  const { apps } = useApps();
  const { remote, logout } = useAuth();
  const { openSettings } = useSettingsPopup();
  const developer = useDeveloperTools();
  const installed = railApps(apps);

  return (
    <nav className="nav-drawer" aria-label="Destinations">
      <p className="section-head">Vela</p>
      {visiblePages(developer).map(
        ({ to, end, label, popup, icon: Icon, weight = 'regular' }, index) =>
          popup ? (
            <button
              key={to}
              type="button"
              className="nav-item"
              ref={index === 0 ? firstRef : undefined}
              aria-haspopup="dialog"
              onClick={() => {
                onClose();
                openSettings();
              }}
            >
              <Icon size={18} weight={weight} aria-hidden="true" />
              <span>{label}</span>
            </button>
          ) : (
            <NavLink
              key={to}
              to={to}
              end={end}
              ref={index === 0 ? firstRef : undefined}
              onClick={onClose}
              className={({ isActive }) => `nav-item${isActive ? ' nav-item-active' : ''}`}
            >
              <Icon size={18} weight={weight} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ),
      )}

      {installed.length > 0 && (
        <>
          <p className="section-head">Your apps</p>
          {installed.map((app) => (
            <NavLink
              key={app.id}
              to={`/app/${app.id}`}
              onClick={onClose}
              className={({ isActive }) => `nav-item${isActive ? ' nav-item-active' : ''}`}
            >
              <AppIcon app={app} size={24} />
              <span>{app.name}</span>
            </NavLink>
          ))}
        </>
      )}

      {remote && (
        <button
          type="button"
          className="nav-item"
          onClick={() => {
            onClose();
            logout();
          }}
        >
          <SignOut size={18} aria-hidden="true" />
          <span>Sign out</span>
        </button>
      )}
    </nav>
  );
}

// The phone navigation: the same rail the desktop shows, sliding in from the
// edge it normally occupies, with a second column beside it. Pages such as Ask
// place their own panel there; everything else gets the labelled list.
// Navigating closes it; Escape and the backdrop return focus to the opener.
export default function NavDrawer({ open, onClose, returnFocusRef, panel }) {
  const firstRef = useRef(null);

  if (!open) return null;

  return (
    <Drawer
      open
      onClose={onClose}
      initialFocusRef={panel ? undefined : firstRef}
      returnFocusRef={returnFocusRef}
      panelClassName={`drawer-nav${panel ? ' drawer-nav-with-panel' : ''}`}
      aria-label="Vela navigation"
    >
      <AppRail onNavigate={onClose} />
      <div className="drawer-nav-panel">
        {panel ?? <NavList onClose={onClose} firstRef={firstRef} />}
      </div>
    </Drawer>
  );
}
