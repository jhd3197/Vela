import { LockSimple, WarningCircle } from '@phosphor-icons/react';
import { useEngine } from '../store.jsx';
import { useDeveloperTools } from '../developer.js';
import GlobalSearch from './GlobalSearch.jsx';
import NotificationBell from './NotificationBell.jsx';
import useMediaQuery from '../hooks/useMediaQuery.js';
import { PHONE } from '../breakpoints.js';

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

// The contextual header above every workspace. Composition is per route: a
// leading control (panel toggle or back), an optional title block, route
// actions, then the connection state.
//
// Search and notifications belong to whichever chrome the layout actually has.
// On a wide screen that is the top bar, which spans every route; here they
// would be a second copy of the same control on the same screen, and two bells
// polling the same feed is not a presentation detail. At phone widths there is
// no top bar, so the header carries them, exactly as it always has. Navigation
// is the rail beside the workspace at every width, so the header never carries
// an opener for it.
export default function WorkspaceHeader({
  lead,
  title,
  subtitle,
  actions,
  search = true,
  compactSearch = false,
}) {
  // No top bar here means this header is the chrome that carries them.
  const owns = useMediaQuery(PHONE);
  return (
    <header className="workspace-header">
      {lead}
      {title && (
        <div className="workspace-heading">
          <span className="workspace-title">{title}</span>
          {subtitle && <span className="workspace-subtitle">{subtitle}</span>}
        </div>
      )}
      {owns && search && !compactSearch && <GlobalSearch />}
      <div className="workspace-header-side">
        {actions}
        {owns && search && compactSearch && <GlobalSearch compact />}
        <ServerBadge />
        {owns && <NotificationBell />}
      </div>
    </header>
  );
}
