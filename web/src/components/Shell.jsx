import { useEffect, useRef } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import {
  ChatCircleText,
  GearSix,
  HardDrives,
  HouseSimple,
  Lightning,
  SquaresFour,
  Storefront,
} from '@phosphor-icons/react';
import { formatBytes } from '../api.js';
import { useApps, useEngine } from '../store.jsx';
import TopBar from './TopBar.jsx';
import Toasts from './Toasts.jsx';

const NAV_ITEMS = [
  { to: '/', label: 'Home', end: true, icon: HouseSimple },
  { to: '/ask', label: 'Ask', icon: ChatCircleText },
  { to: '/apps', label: 'Apps', icon: SquaresFour },
  { to: '/library', label: 'Library', icon: Storefront },
  { to: '/environments', label: 'System', icon: HardDrives, tabHidden: true },
  // Hidden from the mobile tab bar to keep it at five entries with Ask added.
  { to: '/automations', label: 'Automations', icon: Lightning, tabHidden: true },
  { to: '/settings', label: 'Settings', icon: GearSix },
];

// Hub shell: glassy sidebar (brand, nav, storage meter) on desktop, a bottom
// tab bar on mobile, and the shared top bar with search on every page.
export default function Shell({ children }) {
  const pageRef = useRef(null);
  const location = useLocation();
  const { apps, error, refreshApps, toasts, dismissToast } = useApps();
  const { engine } = useEngine();
  const availableCount = (apps || []).filter((a) => !a.installed && a.supported).length;
  const storageBytes = engine?.storage_bytes;
  useEffect(() => {
    if (children) return;
    const page = pageRef.current;
    if (location.state?.restoreLauncher && apps) page.scrollTop = Number(sessionStorage.getItem(`vela.scroll.${location.pathname}`) || 0);
    const save = () => sessionStorage.setItem(`vela.scroll.${location.pathname}`, String(page.scrollTop));
    page.addEventListener('scroll', save);
    return () => page.removeEventListener('scroll', save);
  }, [location.key, Boolean(apps), children]);

  return (
    <div className="layout">
      <div className="ambient-glow" aria-hidden="true" />

      <aside className="sidebar">
        <div className="sidebar-brand">
          <img className="brand-mark" src="/vela-mark.png" alt="" />
          <span className="brand-name">Vela</span>
        </div>
        <nav className="sidebar-nav">
          {NAV_ITEMS.map(({ to, label, end, icon: Icon }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => `nav-item${isActive ? ' nav-item-active' : ''}`}>
              <Icon size={17} weight={label === 'Automations' ? 'fill' : 'regular'} />
              <span>{label}</span>
              {label === 'Library' && availableCount > 0 && <span className="nav-tag">{availableCount} new</span>}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="side-card">
            <div className="side-card-row">
              <span>Storage</span>
              <span>{engine ? formatBytes(storageBytes) : '—'}</span>
            </div>
            <div className="meter">
              <div className="meter-fill" style={{ width: engine ? `${Math.min(100, Math.max(4, (storageBytes / (200 * 1024 ** 3)) * 100))}%` : '4%' }} />
            </div>
            <div className="side-card-status">
              <span className={`dot${engine ? ' dot-ok' : ''}`} />
              {engine ? `Local · ${engine.apps_running ?? 0} running` : 'Connecting…'}
            </div>
          </div>
        </div>
      </aside>

      <div className="main-col">
        <TopBar />
        <main className="page" ref={pageRef}>
          {error && (
            <div className="banner banner-error" role="alert">
              <div>
                <strong>Cannot reach the Vela backend.</strong>
                <p>Make sure the server is running on port 7700, then try again.</p>
              </div>
              <button className="btn" onClick={() => refreshApps()}>Retry</button>
            </div>
          )}
          {children || <Outlet />}
        </main>
      </div>

      {/* Mobile bottom tab bar */}
      <nav className="tabbar">
        {NAV_ITEMS.filter((i) => !i.tabHidden).map(({ to, label, end, icon: Icon }) => (
          <NavLink key={to} to={to} end={end} className={({ isActive }) => `tab-item${isActive ? ' tab-item-active' : ''}`}>
            <Icon size={20} />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>

      <Toasts toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
