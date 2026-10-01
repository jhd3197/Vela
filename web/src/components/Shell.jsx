import { Outlet, useLocation } from 'react-router-dom';
import { useApps } from '../store.jsx';
import AppRail from './AppRail.jsx';
import Toasts from './Toasts.jsx';
import WelcomeSetup from './WelcomeSetup.jsx';
import { useSettingsPopup } from './SettingsProvider.jsx';
import { useGlobalShortcuts } from '../shortcuts.js';
import ShortcutSheet from './ui/ShortcutSheet.jsx';
import TopBar from '../shell/TopBar.jsx';
import useDocumentTitle from '../shell/useDocumentTitle.js';
import DesktopOverview from '../desktops/DesktopOverview.jsx';
import useMediaQuery from '../hooks/useMediaQuery.js';
import { PHONE } from '../breakpoints.js';

// The outer shell: a global top bar, and one persistent rail beside the
// workspace. The rail stays on screen at every width, including phones, so
// every destination and every ready app is one tap away with no hamburger step;
// it sits beside the content rather than over it. The shell renders once around
// every host route and never remounts on navigation. An app workspace that
// keeps the hub's chrome renders its own `Shell` around itself so the same rail
// names and selects it.
//
// The rail runs the full height of the window, and the bar sits above the
// workspace beside it, so the rail reads as one column rather than a strip cut
// off by the bar. The bar is not rendered at phone widths: there the rail is the only chrome, and everything
// the bar carries is still in the workspace header. Whether it is rendered is
// decided here rather than hidden in CSS, so there is one owner per control per
// layout — one bell polling, one search listening for ⌘K — instead of two
// copies with one of them invisible.
export default function Shell({ children }) {
  const location = useLocation();
  const { settingsOpen } = useSettingsPopup();
  const { toasts, dismissToast } = useApps();
  const hasChildren = Boolean(children);
  const phone = useMediaQuery(PHONE);
  // One shell is mounted at a time (the routed pages, or an app workspace that
  // renders its own), so the global shortcut listener is owned here.
  const { shortcutsOpen, closeShortcuts } = useGlobalShortcuts();
  useDocumentTitle();

  return (
    <div className="shell-top">
      <div className="shell">
        <div className="ambient-glow" aria-hidden="true" />
        <AppRail />
        <div className="shell-main">
          {!phone && <TopBar />}
          <div className="workspace">{children || <Outlet />}</div>
        </div>

        <Toasts toasts={toasts} onDismiss={dismissToast} />
        <ShortcutSheet open={shortcutsOpen} onClose={closeShortcuts} />
        <DesktopOverview />
        {!hasChildren && !settingsOpen && <WelcomeSetup key={location.key} />}
      </div>
    </div>
  );
}
