import { Outlet, useLocation } from 'react-router-dom';
import { useApps } from '../store.jsx';
import AppRail from './AppRail.jsx';
import Toasts from './Toasts.jsx';
import WelcomeSetup from './WelcomeSetup.jsx';
import { useSettingsPopup } from './SettingsProvider.jsx';

// The outer shell: one persistent rail beside the workspace. The rail stays on
// screen at every width, including phones, so every destination and every
// ready app is one tap away with no hamburger step; it sits beside the content
// rather than over it. The shell renders once around every host route and
// never remounts on navigation. An app workspace that keeps the hub's chrome
// renders its own `Shell` around itself so the same rail names and selects it.
export default function Shell({ children }) {
  const location = useLocation();
  const { settingsOpen } = useSettingsPopup();
  const { toasts, dismissToast } = useApps();
  const hasChildren = Boolean(children);

  return (
    <div className="shell">
      <div className="ambient-glow" aria-hidden="true" />
      <AppRail />
      <div className="workspace">{children || <Outlet />}</div>

      <Toasts toasts={toasts} onDismiss={dismissToast} />
      {!hasChildren && !settingsOpen && <WelcomeSetup key={location.key} />}
    </div>
  );
}
