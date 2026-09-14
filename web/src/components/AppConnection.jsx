import { useCallback, useEffect, useState } from 'react';
import { api } from '../api.js';
import { useResource } from '../hooks/useResource.js';
import { useAsyncAction } from '../hooks/useAsyncAction.js';
import Button from './ui/Button.jsx';
import FormField from './ui/FormField.jsx';

function ConnectionForm({ app }) {
  const [endpoint, setEndpoint] = useState('http://127.0.0.1:11434');
  const [updated, setUpdated] = useState(null);
  const load = useCallback(options => api.getConnection(app.id, options), [app.id]);
  const { data, error: loadError, loading } = useResource(load);
  const { run, pending, error } = useAsyncAction();
  const status = updated ?? data;

  useEffect(() => {
    if (data?.endpoint) setEndpoint(data.endpoint);
  }, [data]);

  const connect = async () => {
    const result = await run(() => api.bindConnection(app.id, endpoint));
    if (result) setUpdated(result.value);
  };
  const disconnect = async () => {
    const result = await run(() => api.disconnectConnection(app.id));
    if (result) setUpdated(result.value);
  };
  const failure = error || (!updated && loadError);

  return <details className="app-migration" open={!status?.connected}>
    <summary>Ollama connection {status?.connected ? '· Connected' : '· Setup'}</summary>
    <p>Connect to an existing server. The address is reached from your Vela engine. Vela can read model information; it does not manage this server.</p>
    <FormField label="Server address">
      <input id={`endpoint-${app.id}`} className="connection-input" value={endpoint}
        disabled={pending || loading} onChange={event => setEndpoint(event.target.value)} />
    </FormField>
    <div className="actions">
      <Button size="small" pending={pending} disabled={loading} onClick={connect}>
        {pending ? 'Testing…' : 'Test and connect'}
      </Button>
      {status?.connected && <Button size="small" pending={pending} onClick={disconnect}>Disconnect wrapper</Button>}
    </div>
    {failure && <p role="alert">{failure.message}</p>}
  </details>;
}

export default function AppConnection({ app }) {
  if (!app.connection || !app.installed) return null;
  return <ConnectionForm key={app.id} app={app} />;
}
