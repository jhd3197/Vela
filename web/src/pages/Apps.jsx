import LoadingState from '../components/ui/LoadingState.jsx';
import PageHeader from '../components/ui/PageHeader.jsx';
import EmptyState from '../components/ui/EmptyState.jsx';
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Plus } from '@phosphor-icons/react';
import { useApps } from '../store.jsx';
import AppRow from '../components/AppRow.jsx';
import AppDetailDrawer from '../components/AppDetailDrawer.jsx';

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'running', label: 'Running' },
  { key: 'not-installed', label: 'Not installed' },
];

// Apps page per the "All apps" prototype: a segmented filter, installed
// apps grouped in one card list, available apps below, and a dashed
// "Add an app" row that leads to the Library. Rows open the detail drawer.
export default function Apps() {
  const { apps, busyIds, runAction } = useApps();
  const [filter, setFilter] = useState(() => sessionStorage.getItem('vela.apps.filter') || 'all');
  useEffect(() => {
    sessionStorage.setItem('vela.apps.filter', filter);
  }, [filter]);
  const [selected, setSelected] = useState(null);

  const { installed, available } = useMemo(() => {
    const list = apps || [];
    let visible = list;
    if (filter === 'running') visible = list.filter((a) => a.running);
    if (filter === 'not-installed') visible = list.filter((a) => !a.installed);
    return {
      installed: visible.filter((a) => a.installed),
      available: visible.filter((a) => !a.installed),
    };
  }, [apps, filter]);

  // Keep the open drawer in sync with the polled list.
  const selectedLive = selected ? (apps || []).find((a) => a.id === selected.id) || selected : null;
  const empty = apps !== null && installed.length === 0 && available.length === 0;

  return (
    <div className="page-inner">
      <PageHeader
        title="Apps"
        description="Everything on this machine — open, stop, or remove."
        actions={
          <div className="seg" role="tablist">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                role="tab"
                aria-selected={filter === f.key}
                className={`seg-opt${filter === f.key ? ' seg-opt-active' : ''}`}
                onClick={() => setFilter(f.key)}
              >
                {f.label}
              </button>
            ))}
          </div>
        }
      />

      {apps === null && <LoadingState>Loading apps…</LoadingState>}

      {empty && (
        <EmptyState
          title={filter === 'all' ? 'No apps yet' : 'Nothing here'}
          description={
            filter === 'all'
              ? 'Install apps from the Library and they will show up here.'
              : 'No apps match this filter right now.'
          }
        >
          {filter === 'all' && (
            <Link className="btn btn-primary" to="/library">
              Browse Library
            </Link>
          )}
        </EmptyState>
      )}

      {installed.length > 0 && (
        <section style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <h2 className="section-head">Your apps</h2>
          <div className="group-list">
            {installed.map((app) => (
              <AppRow
                key={app.id}
                app={app}
                busy={busyIds.has(app.id)}
                onAction={runAction}
                onDetails={setSelected}
              />
            ))}
          </div>
        </section>
      )}

      {available.length > 0 && (
        <section style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <h2 className="section-head">Available</h2>
          <div className="group-list">
            {available.map((app) => (
              <AppRow
                key={app.id}
                app={app}
                busy={busyIds.has(app.id)}
                onAction={runAction}
                onDetails={setSelected}
                variant="library"
              />
            ))}
          </div>
        </section>
      )}

      {apps !== null && (
        <Link to="/library" className="row-add">
          <Plus size={19} style={{ color: 'var(--accent-strong)' }} />
          <span className="row-add-text">
            <span className="row-add-name">Add an app</span>
            <span className="row-add-sub">From the Library</span>
          </span>
        </Link>
      )}

      <AppDetailDrawer
        app={selectedLive}
        busy={selectedLive ? busyIds.has(selectedLive.id) : false}
        onAction={runAction}
        onClose={() => setSelected(null)}
      />
    </div>
  );
}
