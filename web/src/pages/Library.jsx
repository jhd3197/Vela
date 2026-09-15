import EmptyState from '../components/ui/EmptyState.jsx';
import LoadingState from '../components/ui/LoadingState.jsx';
import PageHeader from '../components/ui/PageHeader.jsx';
import { useMemo, useState } from 'react';
import { MagnifyingGlass } from '@phosphor-icons/react';
import { useApps } from '../store.jsx';
import AppRow from '../components/AppRow.jsx';
import AppDetailDrawer from '../components/AppDetailDrawer.jsx';
import ReleaseImport from '../components/ReleaseImport.jsx';
import ConnectedAppForm from '../components/ConnectedAppForm.jsx';
import WorkspacePage from '../components/WorkspacePage.jsx';

export default function Library() {
  const { apps, busyIds, runAction } = useApps();
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('all');
  const [selected, setSelected] = useState(null);
  const [addingWebApp, setAddingWebApp] = useState(false);

  const categories = useMemo(() => {
    const set = new Set((apps || []).map((a) => a.category).filter(Boolean));
    return ['all', ...Array.from(set).sort()];
  }, [apps]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (apps || []).filter((a) => {
      if (category !== 'all' && a.category !== category) return false;
      if (!q) return true;
      return (
        a.name.toLowerCase().includes(q) ||
        (a.description || '').toLowerCase().includes(q) ||
        (a.author || '').toLowerCase().includes(q)
      );
    });
  }, [apps, query, category]);

  const selectedLive = selected ? (apps || []).find((a) => a.id === selected.id) || selected : null;

  return (
    <WorkspacePage>
      <div className="page-inner">
        <PageHeader
          title="Library"
          description="Install apps or connect an existing web service."
          actions={
            <button className="btn btn-primary" onClick={() => setAddingWebApp(true)}>
              Add web app
            </button>
          }
        />

        <ReleaseImport />
        {addingWebApp && <ConnectedAppForm onClose={() => setAddingWebApp(false)} />}
        <div className="library-controls">
          <div className="searchbox searchbox-inline">
            <MagnifyingGlass className="searchbox-icon" size={15} />
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
                key={c}
                className={`chip${category === c ? ' chip-active' : ''}`}
                onClick={() => setCategory(c)}
              >
                {c === 'all' ? 'All' : c}
              </button>
            ))}
          </div>
        </div>

        {apps === null && <LoadingState>Loading apps…</LoadingState>}

        {apps !== null && visible.length === 0 && (
          <EmptyState
            title="No matches"
            description={
              (apps || []).length === 0
                ? 'The registry is empty on this machine.'
                : 'Try a different search or category.'
            }
          />
        )}

        {visible.length > 0 && (
          <div className="group-list">
            {visible.map((app) => (
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
        )}

        <AppDetailDrawer
          app={selectedLive}
          busy={selectedLive ? busyIds.has(selectedLive.id) : false}
          onAction={runAction}
          onClose={() => setSelected(null)}
        />
      </div>
    </WorkspacePage>
  );
}
