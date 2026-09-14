import Button from './ui/Button.jsx';
import { useCallback, useEffect } from 'react';
import { useResource } from '../hooks/useResource.js';
import { api } from '../api.js';
import { useApps } from '../store.jsx';

export default function ReleaseHistory({ app }) {
  const { reviewRelease } = useApps();
  const load = useCallback((options) => api.releaseHistory(app.id, options), [app.id]);
  const { data, error, loading, refresh } = useResource(load);
  useEffect(() => {
    refresh();
  }, [app.version, app.installed, refresh]);
  const rows = data?.releases ?? [];
  return (
    <section className="drawer-section">
      <h3 className="drawer-section-title">Releases</h3>
      {app.releaseAvailable && (!app.installed || app.version !== app.releaseAvailable.version) && (
        <Button onClick={() => reviewRelease({ app_id: app.id })}>
          Review version {app.releaseAvailable.version}
        </Button>
      )}
      {rows.map((row) => (
        <div className="release-history-row" key={row.id}>
          <span>
            {row.version} · {new Date(row.created_at).toLocaleDateString()}
          </span>
          {row.previous_version && app.installed && (
            <Button
              size="small"
              onClick={() => reviewRelease({ app_id: app.id, rollback: row.id })}
            >
              Review rollback to {row.previous_version}
            </Button>
          )}
        </div>
      ))}
      {!loading && !rows.length && !app.releaseAvailable && (
        <p>No release history yet. Import a newer release from the Library.</p>
      )}
      {error && <p role="alert">{error.message}</p>}
    </section>
  );
}
