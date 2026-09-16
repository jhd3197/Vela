import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ArrowCircleUp, ArrowsClockwise, MagnifyingGlass, Plus } from '@phosphor-icons/react';
import { api } from '../api.js';
import { useApps } from '../store.jsx';
import { useAsyncAction } from '../hooks/useAsyncAction.js';
import { useResource } from '../hooks/useResource.js';
import AppIcon from '../components/AppIcon.jsx';
import AppRow from '../components/AppRow.jsx';
import AppDetailDrawer from '../components/AppDetailDrawer.jsx';
import AddAppDialog from '../components/AddAppDialog.jsx';
import ConnectedAppForm from '../components/ConnectedAppForm.jsx';
import Button from '../components/ui/Button.jsx';
import EmptyState from '../components/ui/EmptyState.jsx';
import LoadingState from '../components/ui/LoadingState.jsx';
import WorkspacePage from '../components/WorkspacePage.jsx';

const TABS = [
  { key: 'discover', label: 'Discover' },
  { key: 'installed', label: 'Installed' },
  { key: 'updates', label: 'Updates' },
];

export function hasUpdate(app) {
  return Boolean(
    app.installed && app.releaseAvailable && app.version !== app.releaseAvailable.version,
  );
}

function cardMeta(app) {
  if (app.kind === 'connected-web') return 'Connected web app';
  return [app.category, app.version && `v${app.version}`].filter(Boolean).join(' · ');
}

// A catalog card for the Discover tab: identity, one line of metadata, a short
// description, and the single action that applies — Install, or Unsupported.
function AppCard({ app, busy, onAction, onDetails, featured }) {
  return (
    <article
      className={`app-card${featured ? ' app-card-featured' : ''}${
        !app.supported ? ' app-card-unsupported' : ''
      }`}
    >
      <button
        type="button"
        className="app-card-open"
        onClick={() => onDetails(app)}
        aria-label={`Details for ${app.name}`}
      >
        <span className="app-card-head">
          <AppIcon app={app} size={featured ? 48 : 40} />
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
        <span className="app-card-state">{app.author ? `By ${app.author}` : 'Available'}</span>
      </div>
    </article>
  );
}

// The Marketplace: one place to find, install, update and remove apps. The URL
// stays `/library`; the active tab rides in `?tab=`. Discover shows the apps
// you do not have yet, Installed is everything on this computer with its detail
// drawer, and Updates gathers the apps with a newer pinned release.
export default function Library() {
  const { apps, busyIds, runAction, refreshApps } = useApps();
  const [params, setParams] = useSearchParams();
  const tab = TABS.some((t) => t.key === params.get('tab')) ? params.get('tab') : 'discover';
  const setTab = (key) => setParams(key === 'discover' ? {} : { tab: key }, { replace: true });

  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('all');
  const [installedFilter, setInstalledFilter] = useState('all'); // all | running
  const [selected, setSelected] = useState(null);
  const [adding, setAdding] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const { data: catalogData, error: catalogError } = useResource(api.catalog);
  const [refreshed, setRefreshed] = useState(null);
  const catalog = refreshed ?? catalogData;
  const { run, pending, error: refreshError } = useAsyncAction();

  const loading = apps === null;
  const list = useMemo(() => apps || [], [apps]);

  const available = useMemo(() => list.filter((app) => !app.installed), [list]);
  const installed = useMemo(() => list.filter((app) => app.installed), [list]);
  const updates = useMemo(() => list.filter(hasUpdate), [list]);

  // Discover categories come from the apps you do not have, so a chip never
  // promises a section that is empty because everything in it is installed.
  const categories = useMemo(() => {
    const counts = new Map();
    for (const app of available)
      if (app.category) counts.set(app.category, (counts.get(app.category) ?? 0) + 1);
    return [
      { key: 'all', label: 'All', count: available.length },
      ...[...counts.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([key, count]) => ({ key, label: key, count })),
    ];
  }, [available]);

  const discover = useMemo(() => {
    const q = query.trim().toLowerCase();
    return available.filter((a) => {
      if (category !== 'all' && a.category !== category) return false;
      if (!q) return true;
      return (
        a.name.toLowerCase().includes(q) ||
        (a.description || '').toLowerCase().includes(q) ||
        (a.author || '').toLowerCase().includes(q)
      );
    });
  }, [available, query, category]);

  // The featured row: the first few discoverable apps that describe themselves,
  // so the row leads with something worth reading rather than a bare name.
  const featured = useMemo(
    () =>
      category === 'all' && !query.trim() ? discover.filter((a) => a.description).slice(0, 3) : [],
    [discover, category, query],
  );
  const featuredIds = new Set(featured.map((a) => a.id));
  const rest = discover.filter((a) => !featuredIds.has(a.id));

  const installedVisible = useMemo(
    () => (installedFilter === 'running' ? installed.filter((a) => a.running) : installed),
    [installed, installedFilter],
  );

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

  // Update all: install each pending release in turn, so a failure part-way
  // through still applies the ones before it and the toast reports the rest.
  const updateAll = () =>
    run(async () => {
      for (const app of updates) {
        await runAction(app.id, 'install');
      }
    });

  return (
    <WorkspacePage
      actions={
        <Button size="small" variant="primary" onClick={() => setAdding(true)}>
          <Plus size={15} aria-hidden="true" />
          <span className="btn-label">Add an app</span>
        </Button>
      }
    >
      <div className="page-inner">
        <header>
          <h1 className="page-title">Marketplace</h1>
          <p className="page-sub">
            Find, install and update the apps that run on this computer, and the web services you
            have connected.
          </p>
        </header>

        <div className="seg" role="tablist" aria-label="Marketplace sections">
          {TABS.map((t) => (
            <button
              key={t.key}
              role="tab"
              aria-selected={tab === t.key}
              className={`seg-opt${tab === t.key ? ' seg-opt-active' : ''}`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
              {t.key === 'updates' && updates.length > 0 ? (
                <span className="seg-count">{updates.length}</span>
              ) : null}
            </button>
          ))}
        </div>

        {loading && <LoadingState>Loading apps…</LoadingState>}

        {!loading && tab === 'discover' && (
          <>
            <div className="library-controls">
              <div className="searchbox searchbox-inline">
                <MagnifyingGlass className="searchbox-icon" size={15} aria-hidden="true" />
                <input
                  type="search"
                  placeholder="Search the Marketplace…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  aria-label="Search the Marketplace"
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
                {`${discover.length} to explore`}
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

            {discover.length === 0 ? (
              <EmptyState
                title={available.length === 0 ? 'You have it all' : 'No matches'}
                description={
                  available.length === 0
                    ? 'Every app in your catalog is installed. Add one from a file, or connect a website you already run.'
                    : 'Try a different search or category.'
                }
              >
                {available.length === 0 ? (
                  <Button variant="primary" onClick={() => setAdding(true)}>
                    Add an app
                  </Button>
                ) : null}
              </EmptyState>
            ) : (
              <>
                {featured.length > 0 && (
                  <section className="featured-row" aria-label="Featured">
                    {featured.map((app) => (
                      <AppCard
                        key={app.id}
                        app={app}
                        featured
                        busy={busyIds.has(app.id)}
                        onAction={runAction}
                        onDetails={setSelected}
                      />
                    ))}
                  </section>
                )}
                {rest.length > 0 && (
                  <div className="card-grid">
                    {rest.map((app) => (
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
              </>
            )}
          </>
        )}

        {!loading && tab === 'installed' && (
          <>
            <div className="seg seg-sub" role="tablist" aria-label="Filter installed apps">
              {[
                { key: 'all', label: 'All' },
                { key: 'running', label: 'Running' },
              ].map((f) => (
                <button
                  key={f.key}
                  role="tab"
                  aria-selected={installedFilter === f.key}
                  className={`seg-opt${installedFilter === f.key ? ' seg-opt-active' : ''}`}
                  onClick={() => setInstalledFilter(f.key)}
                >
                  {f.label}
                </button>
              ))}
            </div>
            {installedVisible.length === 0 ? (
              <EmptyState
                title={installed.length === 0 ? 'No apps yet' : 'Nothing running'}
                description={
                  installed.length === 0
                    ? 'Apps you install from Discover show up here.'
                    : 'No installed app is running right now.'
                }
              >
                {installed.length === 0 && (
                  <Button variant="primary" onClick={() => setTab('discover')}>
                    Discover apps
                  </Button>
                )}
              </EmptyState>
            ) : (
              <div className="group-list">
                {installedVisible.map((app) => (
                  <AppRow
                    key={app.id}
                    app={app}
                    busy={busyIds.has(app.id)}
                    onAction={runAction}
                    onDetails={setSelected}
                  />
                ))}
              </div>
            )}
          </>
        )}

        {!loading && tab === 'updates' && (
          <>
            {updates.length === 0 ? (
              <EmptyState
                title="You are up to date"
                description="Every installed app is on its pinned release."
              />
            ) : (
              <>
                <div className="library-status">
                  <span role="status">
                    {updates.length} {updates.length === 1 ? 'update' : 'updates'} available
                  </span>
                  <Button size="small" variant="primary" pending={pending} onClick={updateAll}>
                    <ArrowCircleUp size={14} aria-hidden="true" />
                    Update all
                  </Button>
                </div>
                <div className="group-list">
                  {updates.map((app) => (
                    <AppRow
                      key={app.id}
                      app={app}
                      busy={busyIds.has(app.id)}
                      onAction={runAction}
                      onDetails={setSelected}
                    />
                  ))}
                </div>
              </>
            )}
          </>
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
