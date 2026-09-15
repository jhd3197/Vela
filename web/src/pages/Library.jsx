import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowCircleUp, ArrowsClockwise, MagnifyingGlass, Plus } from '@phosphor-icons/react';
import { api } from '../api.js';
import { useApps } from '../store.jsx';
import { useAsyncAction } from '../hooks/useAsyncAction.js';
import { useResource } from '../hooks/useResource.js';
import AppIcon from '../components/AppIcon.jsx';
import AppDetailDrawer from '../components/AppDetailDrawer.jsx';
import AddAppDialog from '../components/AddAppDialog.jsx';
import ConnectedAppForm from '../components/ConnectedAppForm.jsx';
import Button from '../components/ui/Button.jsx';
import EmptyState from '../components/ui/EmptyState.jsx';
import WorkspacePage from '../components/WorkspacePage.jsx';

function hasUpdate(app) {
  return Boolean(
    app.installed && app.releaseAvailable && app.version !== app.releaseAvailable.version,
  );
}

function cardMeta(app) {
  if (app.kind === 'connected-web') return 'Connected web app';
  return [app.category, app.version && `v${app.version}`].filter(Boolean).join(' · ');
}

function CardSkeleton() {
  return (
    <article className="app-card app-card-skeleton" aria-hidden="true">
      <div className="app-card-head">
        <span className="skeleton skeleton-icon" />
        <span className="app-card-title">
          <span className="skeleton skeleton-line" />
          <span className="skeleton skeleton-line skeleton-line-short" />
        </span>
      </div>
      <span className="skeleton skeleton-line" />
    </article>
  );
}

// A catalog card per the Library reference: identity, one line of metadata, a
// short description, and the one action that actually applies to this app.
function AppCard({ app, busy, onAction, onDetails }) {
  const update = hasUpdate(app);
  return (
    <article className={`app-card${!app.supported ? ' app-card-unsupported' : ''}`}>
      <button
        type="button"
        className="app-card-open"
        onClick={() => onDetails(app)}
        aria-label={`Details for ${app.name}`}
      >
        <span className="app-card-head">
          <AppIcon app={app} size={40} />
          <span className="app-card-title">
            <span className="app-card-name">{app.name}</span>
            <span className="app-card-meta">{cardMeta(app)}</span>
          </span>
        </span>
        <span className="app-card-blurb">
          {app.supported
            ? app.description || 'No description provided.'
            : 'Not supported on this platform.'}
        </span>
      </button>
      <div className="app-card-foot">
        {!app.supported ? (
          <span className="badge badge-unsupported">Unsupported</span>
        ) : app.installed ? (
          <>
            <Link
              className="btn btn-small btn-primary"
              to={`/app/${app.id}`}
              state={{ returnTo: '/library' }}
            >
              Open
            </Link>
            {update && (
              <Button size="small" pending={busy} onClick={() => onAction(app.id, 'install')}>
                <ArrowCircleUp size={14} aria-hidden="true" />
                Update to v{app.releaseAvailable.version}
              </Button>
            )}
          </>
        ) : (
          <Button
            size="small"
            variant="primary"
            pending={busy}
            onClick={() => onAction(app.id, 'install')}
          >
            Install
          </Button>
        )}
        <span className="app-card-state">
          {app.running
            ? 'Running'
            : app.installed
              ? 'Installed'
              : app.author
                ? `By ${app.author}`
                : 'Available'}
        </span>
      </div>
    </article>
  );
}

export default function Library() {
  const { apps, busyIds, runAction, refreshApps } = useApps();
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('all');
  const [selected, setSelected] = useState(null);
  const [adding, setAdding] = useState(false);
  const [updatesOnly, setUpdatesOnly] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const { data: catalogData, error: catalogError } = useResource(api.catalog);
  const [refreshed, setRefreshed] = useState(null);
  const catalog = refreshed ?? catalogData;
  const { run, pending, error: refreshError } = useAsyncAction();

  const loading = apps === null;
  const list = useMemo(() => apps || [], [apps]);

  const categories = useMemo(() => {
    const counts = new Map();
    for (const app of list)
      if (app.category) counts.set(app.category, (counts.get(app.category) ?? 0) + 1);
    return [
      { key: 'all', label: 'All', count: list.length },
      ...[...counts.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([key, count]) => ({ key, label: key, count })),
    ];
  }, [list]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return list.filter((a) => {
      if (updatesOnly && !hasUpdate(a)) return false;
      if (category !== 'all' && a.category !== category) return false;
      if (!q) return true;
      return (
        a.name.toLowerCase().includes(q) ||
        (a.description || '').toLowerCase().includes(q) ||
        (a.author || '').toLowerCase().includes(q)
      );
    });
  }, [list, query, category, updatesOnly]);

  const updates = list.filter(hasUpdate);
  const selectedLive = selected ? list.find((a) => a.id === selected.id) || selected : null;

  const refresh = async () => {
    const result = await run(async () => {
      const value = await api.refreshCatalog();
      await refreshApps();
      return value;
    });
    if (result) setRefreshed(result.value);
  };
  const catalogNote =
    refreshError?.message || (!refreshed && catalogError?.message) || catalog?.error;

  return (
    <WorkspacePage
      actions={
        <>
          {updates.length > 0 && (
            <Button
              size="small"
              aria-pressed={updatesOnly}
              onClick={() => {
                setUpdatesOnly((value) => !value);
                setCategory('all');
              }}
            >
              <ArrowCircleUp size={15} aria-hidden="true" />
              {updates.length} {updates.length === 1 ? 'update' : 'updates'}
            </Button>
          )}
          <Button size="small" variant="primary" onClick={() => setAdding(true)}>
            <Plus size={15} aria-hidden="true" />
            <span className="btn-label">Add an app</span>
          </Button>
        </>
      }
    >
      <div className="page-inner">
        <header>
          <h1 className="page-title">Library</h1>
          <p className="page-sub">
            Apps that run on this computer, plus the web services you have connected.
          </p>
        </header>

        <div className="library-controls">
          <div className="searchbox searchbox-inline">
            <MagnifyingGlass className="searchbox-icon" size={15} aria-hidden="true" />
            <input
              type="search"
              placeholder="Search the Library…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search library"
            />
          </div>
          <div className="chip-row">
            {categories.map((c) => (
              <button
                key={c.key}
                className={`chip${category === c.key ? ' chip-active' : ''}`}
                aria-pressed={category === c.key}
                onClick={() => setCategory(c.key)}
              >
                {c.label}
                <span className="chip-count">{c.count}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="library-status">
          <span role="status">
            {loading
              ? 'Loading apps…'
              : `${visible.length} of ${list.length} ${list.length === 1 ? 'app' : 'apps'}`}
            {catalog ? ` · ${catalog.releases?.length ?? 0} pinned releases` : ''}
            {catalog?.cached ? ' · cached catalog' : ''}
          </span>
          <Button size="small" pending={pending} onClick={refresh}>
            <ArrowsClockwise size={14} aria-hidden="true" />
            Refresh catalog
          </Button>
        </div>
        {catalogNote && (
          <p className="panel-note" role="status">
            Catalog refresh unavailable: {catalogNote} Cached entries and installed apps remain
            available.
          </p>
        )}

        {loading && (
          <div className="card-grid">
            <CardSkeleton />
            <CardSkeleton />
            <CardSkeleton />
          </div>
        )}

        {!loading && visible.length === 0 && (
          <EmptyState
            title={updatesOnly ? 'No updates' : list.length === 0 ? 'No apps yet' : 'No matches'}
            description={
              updatesOnly
                ? 'Every installed app is on its pinned release.'
                : list.length === 0
                  ? 'No catalog is configured on this computer. Add an app from a release archive, a folder on the server, or connect a web service you already run.'
                  : 'Try a different search or category.'
            }
          >
            {updatesOnly ? (
              <Button onClick={() => setUpdatesOnly(false)}>Show all apps</Button>
            ) : list.length === 0 ? (
              <Button variant="primary" onClick={() => setAdding(true)}>
                Add an app
              </Button>
            ) : null}
          </EmptyState>
        )}

        {!loading && visible.length > 0 && (
          <div className="card-grid">
            {visible.map((app) => (
              <AppCard
                key={app.id}
                app={app}
                busy={busyIds.has(app.id)}
                onAction={runAction}
                onDetails={setSelected}
              />
            ))}
          </div>
        )}
      </div>

      {adding && (
        <AddAppDialog onClose={() => setAdding(false)} onConnect={() => setConnecting(true)} />
      )}
      {connecting && <ConnectedAppForm onClose={() => setConnecting(false)} />}
      <AppDetailDrawer
        app={selectedLive}
        busy={selectedLive ? busyIds.has(selectedLive.id) : false}
        onAction={runAction}
        onClose={() => setSelected(null)}
      />
    </WorkspacePage>
  );
}
