import { useCallback, useState } from 'react';
import { MagnifyingGlass } from '@phosphor-icons/react';
import { api, relTime } from '../api.js';
import { useResource } from '../hooks/useResource.js';
import { useApps } from '../store.jsx';
import Button from './ui/Button.jsx';
import EmptyState from './ui/EmptyState.jsx';
import LoadingState from './ui/LoadingState.jsx';

// System › Errors. Every row is something that actually failed on this
// computer, merged so one repeating failure is one row with a count rather
// than a thousand rows. Adapted from ServerKit's error-log admin view.
const FILTERS = [
  { key: 'open', label: 'Open', query: { resolved: 'false' } },
  { key: 'resolved', label: 'Resolved', query: { resolved: 'true' } },
  { key: 'all', label: 'All', query: {} },
];

const SOURCE_LABELS = { server: 'Engine', dashboard: 'Dashboard', app: 'App' };

export default function ErrorList() {
  const { pushToast } = useApps();
  const [filter, setFilter] = useState('open');
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState(null);
  const [expanded, setExpanded] = useState(null);

  const query = FILTERS.find((entry) => entry.key === filter)?.query || {};
  const load = useCallback(
    (options) => api.getErrors({ ...query, ...(search ? { search } : {}) }, options),
    // The query object is rebuilt each render; its contents are what matter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [filter, search],
  );
  const { data, error, loading, refresh } = useResource(load);

  const act = async (id, run, failure) => {
    setBusy(id);
    try {
      await run();
      await refresh();
    } catch (problem) {
      pushToast(problem.message || failure);
    } finally {
      setBusy(null);
    }
  };

  if (error) {
    return (
      <EmptyState title="Errors are unavailable">
        {error.message} <Button onClick={refresh}>Try again</Button>
      </EmptyState>
    );
  }

  const rows = data?.errors || [];

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Errors</h2>
        <div className="seg" role="tablist" aria-label="Which errors to show">
          {FILTERS.map((entry) => (
            <button
              key={entry.key}
              role="tab"
              aria-selected={filter === entry.key}
              className={`seg-opt${filter === entry.key ? ' seg-opt-active' : ''}`}
              onClick={() => setFilter(entry.key)}
            >
              {entry.label}
            </button>
          ))}
        </div>
      </div>

      <div className="searchbox searchbox-inline">
        <MagnifyingGlass className="searchbox-icon" size={15} aria-hidden="true" />
        <input
          type="search"
          value={search}
          placeholder="Search errors"
          aria-label="Search errors"
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>

      {loading && rows.length === 0 ? (
        <LoadingState>Looking for errors…</LoadingState>
      ) : rows.length === 0 ? (
        <p className="panel-note">
          {search || filter !== 'open'
            ? 'Nothing matches that.'
            : 'Nothing has failed. Vela records errors here as they happen — it does not send them anywhere.'}
        </p>
      ) : (
        <ul className="error-list">
          {rows.map((row) => (
            <li key={row.id} className={`error-row${row.resolved ? ' error-row-resolved' : ''}`}>
              <div className="error-head">
                <span className={`chip chip-${row.source}`}>
                  {SOURCE_LABELS[row.source] || row.source}
                </span>
                <span className="error-message">{row.message}</span>
                {row.count > 1 && (
                  <span className="error-count" title={`Seen ${row.count} times`}>
                    ×{row.count}
                  </span>
                )}
                <span className="error-when">{relTime(row.lastSeen)}</span>
              </div>
              <div className="error-meta">
                {row.type && <span className="mono">{row.type}</span>}
                {row.endpoint && <span className="mono">{row.endpoint}</span>}
              </div>
              {row.traceback && (
                <>
                  <button
                    type="button"
                    className="error-toggle"
                    aria-expanded={expanded === row.id}
                    onClick={() => setExpanded(expanded === row.id ? null : row.id)}
                  >
                    {expanded === row.id ? 'Hide details' : 'Show details'}
                  </button>
                  {expanded === row.id && <pre className="error-trace">{row.traceback}</pre>}
                </>
              )}
              <div className="error-actions">
                <Button
                  size="small"
                  variant="ghost"
                  pending={busy === row.id}
                  onClick={() =>
                    act(
                      row.id,
                      () => api.resolveError(row.id, !row.resolved),
                      'Could not change that error.',
                    )
                  }
                >
                  {row.resolved ? 'Reopen' : 'Resolve'}
                </Button>
                <Button
                  size="small"
                  variant="ghost"
                  pending={busy === row.id}
                  onClick={() =>
                    act(row.id, () => api.deleteError(row.id), 'Could not delete that error.')
                  }
                >
                  Delete
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {data && data.total > rows.length && (
        <p className="panel-note">
          Showing {rows.length} of {data.total}. Vela keeps the last 500 errors, or 30 days,
          whichever comes first.
        </p>
      )}
    </section>
  );
}
