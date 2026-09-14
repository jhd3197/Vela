import { useState } from 'react';
import { api } from '../api.js';
import { useApps } from '../store.jsx';
import { useAsyncAction } from '../hooks/useAsyncAction.js';
import { useResource } from '../hooks/useResource.js';
import Button from './ui/Button.jsx';

export default function ReleaseImport() {
  const { reviewRelease, refreshApps } = useApps();
  const [folder, setFolder] = useState('');
  const { data, error: loadError } = useResource(api.catalog);
  const [updated, setUpdated] = useState(null);
  const catalog = updated ?? data;
  const [error, setError] = useState('');
  const { run, pending, error: actionError } = useAsyncAction();

  const refresh = async () => {
    setError('');
    const result = await run(async () => {
      const value = await api.refreshCatalog();
      await refreshApps();
      return value;
    });
    if (result) setUpdated(result.value);
  };
  const failure = error || actionError?.message || (!updated && loadError?.message);

  return <details className="release-import">
    <summary>Import apps &amp; refresh catalog</summary>
    <p>Choose a release ZIP from this device, or enter an app folder on the engine. You’ll review its permissions and data changes before installation.</p>
    <label>Release archive<input type="file" accept=".zip,application/zip" onChange={event => {
      const file = event.target.files?.[0];
      if (!file) return;
      if (file.size > 32 * 1024 * 1024) { setError('Release archive exceeds 32 MiB.'); return; }
      setError('');
      reviewRelease({ file });
      event.target.value = '';
    }} /></label>
    <form onSubmit={event => {
      event.preventDefault();
      if (folder.trim()) reviewRelease({ folder: folder.trim() });
    }}>
      <label htmlFor="release-folder">App folder on engine</label>
      <div className="release-import-path">
        <input id="release-folder" value={folder} onChange={event => setFolder(event.target.value)}
          required placeholder="Absolute path to a folder containing app.json" />
        <Button type="submit">Review folder</Button>
      </div>
    </form>
    <p>{catalog?.cached ? 'Using cached catalog.' : 'Catalog ready.'} {catalog?.releases?.length ?? 0} pinned releases.</p>
    {catalog?.error && <p role="status">Refresh unavailable: {catalog.error} Cached entries and installed apps remain available.</p>}
    <Button size="small" pending={pending} onClick={refresh}>Refresh catalog</Button>
    {failure && <p role="alert">{failure}</p>}
  </details>;
}
