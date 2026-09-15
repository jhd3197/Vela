import { useCallback, useMemo, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { phoneTabs } from '../navigation.js';
import { ShellContext } from '../shell-context.js';
import { useApps } from '../store.jsx';
import AppRail from './AppRail.jsx';
import NavDrawer from './NavDrawer.jsx';
import Toasts from './Toasts.jsx';
import WelcomeSetup from './WelcomeSetup.jsx';
import { useSettingsPopup } from './SettingsProvider.jsx';

// The outer shell: one persistent rail beside the workspace on desktop, the
// same destinations in a drawer plus a bottom tab bar on phones. It renders
// once around every host route and never remounts on navigation.
export default function Shell({ children }) {
  const location = useLocation();
  const { openSettings, settingsOpen } = useSettingsPopup();
  const { toasts, dismissToast } = useApps();
  const [navOpen, setNavOpen] = useState(false);
  const openNav = useCallback(() => setNavOpen(true), []);
  const shell = useMemo(() => ({ openNav }), [openNav]);
  const hasChildren = Boolean(children);

  return (
    <ShellContext.Provider value={shell}>
      <div className="shell">
        <div className="ambient-glow" aria-hidden="true" />
        <AppRail />
        <div className="workspace">{children || <Outlet />}</div>

        <nav className="tabbar" aria-label="Sections">
          {phoneTabs.map(({ to, label, end, popup, icon: Icon }) =>
            popup ? (
              <button
                key={to}
                type="button"
                className="tab-item"
                aria-haspopup="dialog"
                onClick={() => openSettings()}
              >
                <Icon size={20} aria-hidden="true" />
                <span>{label}</span>
              </button>
            ) : (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) => `tab-item${isActive ? ' tab-item-active' : ''}`}
              >
                <Icon size={20} aria-hidden="true" />
                <span>{label}</span>
              </NavLink>
            ),
          )}
        </nav>

        <NavDrawer open={navOpen} onClose={() => setNavOpen(false)} />
        <Toasts toasts={toasts} onDismiss={dismissToast} />
        {!hasChildren && !settingsOpen && <WelcomeSetup key={location.key} />}
      </div>
    </ShellContext.Provider>
  );
}
