import { List, LockSimple } from '@phosphor-icons/react';
import { useEngine } from '../store.jsx';
import GlobalSearch from './GlobalSearch.jsx';
import NotificationBell from './NotificationBell.jsx';

// The host badge doubles as the server indicator the sidebar used to carry:
// where Vela is served from, and whether the engine is actually answering.
function ServerBadge() {
  const { engine, engineError } = useEngine();
  const online = Boolean(engine) && !engineError;
  const state = engineError ? 'Server unreachable' : online ? 'Server online' : 'Connecting…';
  return (
    <span className={`host-badge${online ? ' host-badge-ok' : ''}`} role="status">
      <LockSimple size={13} aria-hidden="true" />
      <span className="host-badge-name">{window.location.host}</span>
      <span className="sr-only">{state}</span>
    </span>
  );
}

// The contextual header above every workspace. Composition is per route:
// a leading control (panel toggle or back), an optional title block, either the
// global search or route actions, then the server badge and notifications that
// must stay reachable everywhere.
export default function WorkspaceHeader({
  lead,
  title,
  subtitle,
  actions,
  search = true,
  onOpenNav,
}) {
  return (
    <header className="workspace-header">
      <button
        type="button"
        className="btn btn-icon workspace-nav-button"
        aria-label="Open navigation"
        aria-haspopup="dialog"
        onClick={onOpenNav}
      >
        <List size={18} aria-hidden="true" />
      </button>
      {lead}
      {title && (
        <div className="workspace-heading">
          <span className="workspace-title">{title}</span>
          {subtitle && <span className="workspace-subtitle">{subtitle}</span>}
        </div>
      )}
      {search && <GlobalSearch />}
      <div className="workspace-header-side">
        {actions}
        <ServerBadge />
        <NotificationBell />
      </div>
    </header>
  );
}
