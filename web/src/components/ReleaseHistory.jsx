import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { useApps } from '../store.jsx';

export default function ReleaseHistory({ app }) {
  const [rows, setRows] = useState([]);
  const [error, setError] = useState('');
  const { reviewRelease } = useApps();
  useEffect(() => {
    let cancelled = false;
    api.releaseHistory(app.id).then(value => { if (!cancelled) setRows(value.releases); }).catch(failure => { if (!cancelled) setError(failure.message); });
    return () => { cancelled = true; };
  }, [app.id, app.version, app.installed]);
  return <section className="drawer-section"><h3 className="drawer-section-title">Releases</h3>
    {app.releaseAvailable && (!app.installed || app.version !== app.releaseAvailable.version) && <button className="btn" onClick={() => reviewRelease({ app_id: app.id })}>Review version {app.releaseAvailable.version}</button>}
    {rows.map(row => <div className="release-history-row" key={row.id}><span>{row.version} · {new Date(row.created_at).toLocaleDateString()}</span>{row.previous_version && app.installed && <button className="btn btn-small" onClick={() => reviewRelease({ app_id: app.id, rollback: row.id })}>Review rollback to {row.previous_version}</button>}</div>)}
    {!rows.length && !app.releaseAvailable && <p>No release history yet. Import a newer release from the Library.</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
