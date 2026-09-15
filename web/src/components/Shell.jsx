import { useCallback, useMemo, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { ShellContext } from '../shell-context.js';
import { useApps } from '../store.jsx';
import AppRail from './AppRail.jsx';
import NavDrawer from './NavDrawer.jsx';
import Toasts from './Toasts.jsx';
import WelcomeSetup from './WelcomeSetup.jsx';
import { useSettingsPopup } from './SettingsProvider.jsx';

// The outer shell: one persistent rail beside the workspace on desktop. On
// phones the same rail slides in from the left edge inside a drawer, so the
// destinations and installed apps live in one place at every width. It
// renders once around every host route and never remounts on navigation.
export default function Shell({ children }) {
  const location = useLocation();
  const { settingsOpen } = useSettingsPopup();
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

        <NavDrawer open={navOpen} onClose={() => setNavOpen(false)} />
        <Toasts toasts={toasts} onDismiss={dismissToast} />
        {!hasChildren && !settingsOpen && <WelcomeSetup key={location.key} />}
      </div>
    </ShellContext.Provider>
  );
}
