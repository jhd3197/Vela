import { useState } from 'react';
import { api } from '../api.js';

export default function AppDataMigration({ app }) {
  const [raw, setRaw] = useState('');
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const spec = app.legacyStorage;
  if (!spec || !(spec.keys ? Object.values(spec.keys) : [spec.key]).every(key => key?.startsWith(`vela.${app.id}.`))) return null;
  const loadBrowser = () => {
    try {
      const value = spec.keys ? JSON.stringify(Object.fromEntries(Object.entries(spec.keys).map(([field, key]) => [field, JSON.parse(localStorage.getItem(key))]))) : localStorage.getItem(spec.key);
      if (value === null) throw new Error('No earlier data at this browser address. You can upload an export from the old address instead.');
      if (spec.keys && Object.values(JSON.parse(value)).every(item => item === null)) throw new Error('No earlier data at this browser address. Choose an exported recovery file instead.');
      setRaw(value); setPreview(null); setError(''); setMessage('Browser copy selected; nothing has been imported.');
    } catch (failure) { setError(failure.message); }
  };
  const inspect = async () => {
    setBusy(true); setError('');
    try { setPreview(await api.previewMigration(app.id, JSON.parse(raw))); setMessage(''); }
    catch (failure) { setPreview(null); setError(failure.message); }
    finally { setBusy(false); }
  };
  const migrate = async () => {
    setBusy(true); setError('');
    try {
      const result = await api.migrate(app.id, JSON.parse(raw), preview.revision);
      setMessage(result.alreadyImported ? 'This copy has already been imported.' : spec.keys ? 'Earlier copy retained. Choose it inside the app to apply it. The browser original is unchanged.' : 'Import verified. The original browser copy and engine recovery copy are retained.');
      setPreview(null);
    } catch (failure) { setError(failure.message + ' Review again to use the latest engine revision.'); setPreview(null); }
    finally { setBusy(false); }
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([raw], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `${app.id}-browser-recovery.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return <details className="app-migration"><summary>Import earlier {app.name} data</summary>
    <p>{spec.keys ? 'Keep an earlier browser copy on your engine, then choose it inside the app. Your current saved data stays intact until you apply it.' : 'Bring records from this browser or an exported file into your engine. Existing engine records stay intact; conflicting records become separate copies.'}</p>
    <div className="actions"><button className="btn btn-small" onClick={loadBrowser}>Use this browser’s earlier data</button><label className="btn btn-small">Choose recovery file<input type="file" accept=".json,application/json" onChange={async event => {
      const file = event.target.files?.[0]; if (!file) return;
      if (file.size > 1048576) { setError('The file exceeds 1 MiB.'); return; }
      setRaw(await file.text()); setPreview(null); setError(''); setMessage('File selected; review before importing.');
    }} /></label></div>
    {raw && <div className="actions"><button className="btn btn-small" onClick={download}>Download recovery copy</button><button className="btn btn-small" disabled={busy} onClick={inspect}>Review import</button></div>}
    {preview && <p>{preview.alreadyImported ? 'This copy has already been imported.' : `${preview.added} records to add, including ${preview.conflicts} conflicting records kept as separate copies.`}</p>}
    {preview && !preview.alreadyImported && <button className="btn btn-primary" disabled={busy} onClick={migrate}>Import and keep both versions</button>}
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
  </details>;
}
