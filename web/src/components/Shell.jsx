import Button from './ui/Button.jsx';
import { dashboardPages } from '../navigation.js';
import { useEffect, useRef } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useApps, useEngine } from '../store.jsx';
import TopBar from './TopBar.jsx';
import Toasts from './Toasts.jsx';
import WelcomeSetup from './WelcomeSetup.jsx';
import { useSettingsPopup } from './SettingsProvider.jsx';

// Hub shell: glassy sidebar (brand, nav, server status) on desktop, a bottom
// tab bar on mobile, and the shared top bar with search on every page.
export default function Shell({ children }) {
  const pageRef = useRef(null);
  const location = useLocation();
  const { openSettings, settingsOpen } = useSettingsPopup();
  const { apps, error, refreshApps, toasts, dismissToast } = useApps();
  const { engine, engineError } = useEngine();
  const serverOnline = Boolean(engine) && !engineError;
  const runningCount = engine?.apps_running ?? 0;
  const availableCount = (apps || []).filter((a) => !a.installed && a.supported).length;
  const hasApps = Boolean(apps);
  const hasChildren = Boolean(children);
  useEffect(() => {
    if (hasChildren) return;
    const page = pageRef.current;
    if (location.state?.restoreLauncher && hasApps)
      page.scrollTop = Number(sessionStorage.getItem(`vela.scroll.${location.pathname}`) || 0);
    const save = () =>
      sessionStorage.setItem(`vela.scroll.${location.pathname}`, String(page.scrollTop));
    page.addEventListener('scroll', save);
    return () => page.removeEventListener('scroll', save);
  }, [location.key, location.pathname, location.state?.restoreLauncher, hasApps, hasChildren]);

  return (
    <div className={`layout${location.pathname === '/ask' ? ' layout-ask' : ''}`}>
      <div className="ambient-glow" aria-hidden="true" />

      <aside className="sidebar">
        <div className="sidebar-brand">
          <img className="brand-mark" src="/vela-mark.png" alt="" />
          <span className="brand-name">Vela</span>
        </div>
        <nav className="sidebar-nav">
          {dashboardPages.map(({ to, label, end, popup, icon: Icon, weight = 'regular' }) =>
            popup ? (
              <button
                key={to}
                type="button"
                className="nav-item"
                aria-haspopup="dialog"
                onClick={() => openSettings()}
              >
                <Icon size={17} weight={weight} />
                <span>{label}</span>
              </button>
            ) : (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) => `nav-item${isActive ? ' nav-item-active' : ''}`}
              >
                <Icon size={17} weight={weight} />
                <span>{label}</span>
                {label === 'Library' && availableCount > 0 && (
                  <span className="nav-tag">{availableCount} new</span>
                )}
              </NavLink>
            ),
          )}
        </nav>
        <div className="sidebar-foot">
          <div className="side-card" role="status">
            <div className="side-card-status">
              <span className={`dot${serverOnline ? ' dot-ok' : ''}`} aria-hidden="true" />
              {engineError ? 'Server unreachable' : serverOnline ? 'Server online' : 'Connecting…'}
            </div>
            <span className="side-card-detail">
              {engineError
                ? 'Waiting to reconnect'
                : serverOnline
                  ? `${runningCount} ${runningCount === 1 ? 'app' : 'apps'} running`
                  : 'Checking your Vela server'}
            </span>
          </div>
        </div>
      </aside>

      <div className="main-col">
        <TopBar />
        <main className={`page${location.pathname === '/ask' ? ' page-ask' : ''}`} ref={pageRef}>
          {error && (
            <div className="banner banner-error" role="alert">
              <div>
                <strong>Cannot reach the Vela backend.</strong>
                <p>Make sure the server is running on port 7700, then try again.</p>
              </div>
              <Button onClick={() => refreshApps()}>Retry</Button>
            </div>
          )}
          {children || <Outlet />}
        </main>
      </div>

      {/* Mobile bottom tab bar */}
      <nav className="tabbar">
        {dashboardPages
          .filter((i) => !i.tabHidden)
          .map(({ to, label, end, popup, icon: Icon }) =>
            popup ? (
              <button
                key={to}
                type="button"
                className="tab-item"
                aria-haspopup="dialog"
                onClick={() => openSettings()}
              >
                <Icon size={20} />
                <span>{label}</span>
              </button>
            ) : (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) => `tab-item${isActive ? ' tab-item-active' : ''}`}
              >
                <Icon size={20} />
                <span>{label}</span>
              </NavLink>
            ),
          )}
      </nav>

      <Toasts toasts={toasts} onDismiss={dismissToast} />
      {!hasChildren && !settingsOpen && <WelcomeSetup key={location.key} />}
    </div>
  );
}
