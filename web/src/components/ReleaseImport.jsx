import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { useApps } from '../store.jsx';

export default function ReleaseImport() {
  const { reviewRelease, refreshApps } = useApps();
  const [folder, setFolder] = useState('');
  const [catalog, setCatalog] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.catalog().then(setCatalog).catch(failure => setError(failure.message)); }, []);
  return <details className="release-import"><summary>Import apps &amp; refresh catalog</summary>
    <p>Choose a release ZIP from this device, or enter an app folder on the engine. You’ll review its permissions and data changes before installation.</p>
    <label>Release archive<input type="file" accept=".zip,application/zip" onChange={event => {
      const file = event.target.files?.[0]; if (!file) return;
      if (file.size > 32 * 1024 * 1024) { setError('Release archive exceeds 32 MiB.'); return; }
      reviewRelease({ file }); event.target.value = '';
    }} /></label>
    <form onSubmit={event => { event.preventDefault(); if (folder.trim()) reviewRelease({ folder: folder.trim() }); }}>
      <label htmlFor="release-folder">App folder on engine</label><div className="release-import-path"><input id="release-folder" value={folder} onChange={event => setFolder(event.target.value)} required placeholder="Absolute path to a folder containing app.json" /><button className="btn" type="submit">Review folder</button></div>
    </form>
    <p>{catalog?.cached ? 'Using cached catalog.' : 'Catalog ready.'} {catalog?.releases?.length ?? 0} pinned releases.</p>
    {catalog?.error && <p role="status">Refresh unavailable: {catalog.error} Cached entries and installed apps remain available.</p>}
    <button className="btn btn-small" disabled={busy} onClick={async () => { setBusy(true); try { setCatalog(await api.refreshCatalog()); await refreshApps(); } catch (failure) { setError(failure.message); } finally { setBusy(false); } }}>Refresh catalog</button>
    {error && <p role="alert">{error}</p>}
  </details>;
}
