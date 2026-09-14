import { useEffect, useState } from 'react';
import { api } from '../api.js';

export default function AppConnection({ app }) {
  const [endpoint, setEndpoint] = useState('http://127.0.0.1:11434');
  const [status, setStatus] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (app.installed && app.connection) api.getConnection(app.id).then(value => { setStatus(value); if (value.endpoint) setEndpoint(value.endpoint); }).catch(failure => setError(failure.message)); }, [app.id, app.installed]);
  if (!app.connection || !app.installed) return null;
  const bind = async () => {
    setBusy(true); setError('');
    try { setStatus(await api.bindConnection(app.id, endpoint)); }
    catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  };
  return <details className="app-migration" open={!status?.connected}><summary>Ollama connection {status?.connected ? '· Connected' : '· Setup'}</summary>
    <p>Connect to an existing server. The address is reached from your Vela engine. Vela can read model information; it does not manage this server.</p>
    <label htmlFor={`endpoint-${app.id}`}>Server address</label><input id={`endpoint-${app.id}`} className="connection-input" value={endpoint} onChange={event => setEndpoint(event.target.value)} />
    <div className="actions"><button className="btn btn-small" disabled={busy} onClick={bind}>{busy ? 'Testing…' : 'Test and connect'}</button>
    {status?.connected && <button className="btn btn-small" disabled={busy} onClick={async () => { try { setStatus(await api.disconnectConnection(app.id)); } catch (failure) { setError(failure.message); } }}>Disconnect wrapper</button>}</div>
    {error && <p role="alert">{error}</p>}
  </details>;
}
