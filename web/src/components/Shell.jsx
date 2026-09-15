import { useCallback, useMemo, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { ShellContext } from '../shell-context.js';
import { useApps } from '../store.jsx';
import AppRail from './AppRail.jsx';
import NavDrawer from './NavDrawer.jsx';
import Toasts from './Toasts.jsx';
import WelcomeSetup from './WelcomeSetup.jsx';
import { useSettingsPopup } from './SettingsProvider.jsx';

// The outer shell: one persistent rail beside the workspace. Home keeps that
// rail on screen at every width, including phones, so a ready app is one tap
// away with no hamburger step; it sits beside the content rather than over it.
// Other workspaces move it into the drawer opened from their header. The shell
// renders once around every host route and never remounts on navigation.
export default function Shell({ children }) {
  const location = useLocation();
  const railFixed = location.pathname === '/';
  const { settingsOpen } = useSettingsPopup();
  const { toasts, dismissToast } = useApps();
  const [navOpen, setNavOpen] = useState(false);
  const openNav = useCallback(() => setNavOpen(true), []);
  const shell = useMemo(() => ({ openNav, railFixed }), [openNav, railFixed]);
  const hasChildren = Boolean(children);

  return (
    <ShellContext.Provider value={shell}>
      <div className={`shell${railFixed ? ' shell-rail-fixed' : ''}`}>
        <div className="ambient-glow" aria-hidden="true" />
        <AppRail />
        <div className="workspace">{children || <Outlet />}</div>

        <NavDrawer open={navOpen} onClose={() => setNavOpen(false)} />
        <Toasts toasts={toasts} onDismiss={dismissToast} />
        {!hasChildren && !settingsOpen && <WelcomeSetup key={location.key} />}
      </div>
    </ShellContext.Provider>
  );
}
