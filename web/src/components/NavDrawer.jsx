import { useRef } from 'react';
import { NavLink } from 'react-router-dom';
import { SignOut } from '@phosphor-icons/react';
import { dashboardPages } from '../navigation.js';
import { useApps } from '../store.jsx';
import { useAuth } from './AuthGate.jsx';
import { useSettingsPopup } from './SettingsProvider.jsx';
import AppIcon from './AppIcon.jsx';
import Drawer from './ui/Drawer.jsx';
import { railApps } from './AppRail.jsx';

// The phone equivalent of the rail: the same destinations and installed-app
// shortcuts, with visible labels. Navigating closes it; Escape and the
// backdrop return focus to the control that opened it.
export default function NavDrawer({ open, onClose, returnFocusRef }) {
  const { apps } = useApps();
  const { remote, logout } = useAuth();
  const { openSettings } = useSettingsPopup();
  const installed = railApps(apps);
  const firstRef = useRef(null);

  if (!open) return null;

  return (
    <Drawer
      open
      onClose={onClose}
      initialFocusRef={firstRef}
      returnFocusRef={returnFocusRef}
      panelClassName="drawer-nav"
      aria-label="Vela navigation"
    >
      <nav className="nav-drawer">
        <p className="section-head">Vela</p>
        {dashboardPages.map(({ to, end, label, popup, icon: Icon, weight = 'regular' }, index) =>
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
    </Drawer>
  );
}
