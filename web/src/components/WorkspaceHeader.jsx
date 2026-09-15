import { List, LockSimple, WarningCircle } from '@phosphor-icons/react';
import { useEngine } from '../store.jsx';
import { useDeveloperTools } from '../developer.js';
import GlobalSearch from './GlobalSearch.jsx';
import NotificationBell from './NotificationBell.jsx';

// Where Vela is served from is a developer fact, so a healthy server says
// nothing at all. A confirmed failure — the engine resource actually errored —
// says so in plain words and offers the retry. A missing or still-loading
// answer is neither an outage nor a success, so it stays silent.
function ServerBadge() {
  const developer = useDeveloperTools();
  const { engine, engineError, engineRefreshing, refreshEngine } = useEngine();

  if (engineError) {
    return (
      <span className="connection-alert" role="alert">
        <WarningCircle size={14} aria-hidden="true" />
        <span>Can’t connect to Vela</span>
        <button
          type="button"
          className="btn btn-small"
          disabled={engineRefreshing}
          onClick={() => refreshEngine()}
        >
          {engineRefreshing ? 'Retrying…' : 'Retry'}
        </button>
      </span>
    );
  }

  if (!developer || !engine) return null;

  return (
    <span className="host-badge host-badge-ok" role="status">
      <LockSimple size={13} aria-hidden="true" />
      <span className="host-badge-name">{window.location.host}</span>
      <span className="sr-only">Server online</span>
    </span>
  );
}

// The contextual header above every workspace. Composition is per route:
// the phone drawer opener (unless the page supplies its own), a leading
// control (panel toggle or back), an optional title block, either the
// global search or route actions, then the connection state and notifications
// that must stay reachable everywhere.
export default function WorkspaceHeader({
  lead,
  title,
  subtitle,
  actions,
  search = true,
  compactSearch = false,
  onOpenNav,
}) {
  return (
    <header className="workspace-header">
      {onOpenNav && (
        <button
          type="button"
          className="btn btn-icon workspace-nav-button"
          aria-label="Open navigation"
          aria-haspopup="dialog"
          onClick={onOpenNav}
        >
          <List size={18} aria-hidden="true" />
        </button>
      )}
      {lead}
      {title && (
        <div className="workspace-heading">
          <span className="workspace-title">{title}</span>
          {subtitle && <span className="workspace-subtitle">{subtitle}</span>}
        </div>
      )}
      {search && !compactSearch && <GlobalSearch />}
      <div className="workspace-header-side">
        {actions}
        {search && compactSearch && <GlobalSearch compact />}
        <ServerBadge />
        <NotificationBell />
      </div>
    </header>
  );
}
