import { useCallback, useEffect, useState } from 'react';
import { api } from '../api.js';
import { useResource } from '../hooks/useResource.js';
import { useAsyncAction } from '../hooks/useAsyncAction.js';
import Button from './ui/Button.jsx';
import FormField from './ui/FormField.jsx';

function ConnectionForm({ app, setupOnly }) {
  const [endpoint, setEndpoint] = useState('http://127.0.0.1:11434');
  const [updated, setUpdated] = useState(null);
  const load = useCallback((options) => api.getConnection(app.id, options), [app.id]);
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

  // Above the app, only an unfinished setup earns space. Once the service is
  // connected the form stays available in the app's settings.
  if (setupOnly && status?.connected) return null;

  return (
    <details className="app-migration" open={!status?.connected}>
      <summary>Ollama connection {status?.connected ? '· Connected' : '· Setup'}</summary>
      <p>
        Connect to an existing server. The address is reached from your Vela engine. Vela can read
        model information; it does not manage this server.
      </p>
      <FormField label="Server address">
        <input
          id={`endpoint-${app.id}`}
          className="connection-input"
          value={endpoint}
          disabled={pending || loading}
          onChange={(event) => setEndpoint(event.target.value)}
        />
      </FormField>
      <div className="actions">
        <Button size="small" pending={pending} disabled={loading} onClick={connect}>
          {pending ? 'Testing…' : 'Test and connect'}
        </Button>
        {status?.connected && (
          <Button size="small" pending={pending} onClick={disconnect}>
            Disconnect
          </Button>
        )}
      </div>
      {status?.connected && (
        <p className="panel-note">
          Disconnecting stops {app.name} reaching this server. It does not change the server itself,
          and you can connect it again here.
        </p>
      )}
      {failure && <p role="alert">{failure.message}</p>}
    </details>
  );
}

// An app that talks to a public API. The address is fixed by its manifest; the
// owner only supplies the secret, which is sent from the engine and never
// shown again — not to the app, and not back to this form.
function SecretForm({ app, setupOnly }) {
  const [secret, setSecret] = useState('');
  const [updated, setUpdated] = useState(null);
  const load = useCallback((options) => api.getConnection(app.id, options), [app.id]);
  const { data, error: loadError, loading } = useResource(load);
  const { run, pending, error } = useAsyncAction();
  const status = updated ?? data;
  const spec = status?.secret ?? app.connection.secret;
  const host = new URL(app.connection.baseUrl).host;
  const configured = Boolean(status?.secret?.configured);

  const save = async () => {
    const result = await run(() => api.saveConnectionSecret(app.id, secret.trim()));
    if (result) {
      setUpdated(result.value);
      setSecret('');
    }
  };
  const remove = async () => {
    const result = await run(() => api.disconnectConnection(app.id));
    if (result) setUpdated(await api.getConnection(app.id));
  };
  const failure = error || (!updated && loadError);

  if (!spec) return null;
  if (setupOnly && (configured || !spec.required)) return null;

  return (
    <details className="app-migration" open={!configured && Boolean(spec.required)}>
      <summary>
        {host} · {configured ? `${spec.label} saved` : spec.required ? 'Setup' : 'Optional'}
      </summary>
      <p>
        {app.name} reaches {host} through your Vela engine. {spec.description} The value is kept on
        this Vela and added to requests there; {app.name} never sees it.
      </p>
      <FormField label={spec.label}>
        <input
          id={`secret-${app.id}`}
          className="connection-input"
          type="password"
          autoComplete="off"
          spellCheck={false}
          placeholder={configured ? 'Saved — paste a new value to replace it' : spec.placeholder}
          value={secret}
          disabled={pending || loading}
          onChange={(event) => setSecret(event.target.value)}
        />
      </FormField>
      <div className="actions">
        <Button size="small" pending={pending} disabled={loading || !secret.trim()} onClick={save}>
          {configured ? 'Replace' : 'Save'}
        </Button>
        {configured && (
          <Button size="small" pending={pending} onClick={remove}>
            Remove
          </Button>
        )}
      </div>
      {failure && <p role="alert">{failure.message}</p>}
    </details>
  );
}

// An app whose service is self-hosted: the owner supplies the address (a
// public https URL, or a LAN address), then the secret. Changing the address
// clears the saved key — the engine drops it, because the old key belongs to
// the old origin — and this says so where the change is made.
function SelfHostedForm({ app, setupOnly }) {
  const [endpoint, setEndpoint] = useState('');
  const [updated, setUpdated] = useState(null);
  const load = useCallback((options) => api.getConnection(app.id, options), [app.id]);
  const { data, error: loadError, loading } = useResource(load);
  const { run, pending, error } = useAsyncAction();
  const status = updated ?? data;

  useEffect(() => {
    if (data) setEndpoint(data.endpoint || '');
  }, [data]);

  const saveAddress = async () => {
    const result = await run(() => api.bindConnection(app.id, endpoint.trim()));
    if (result) setUpdated(result.value);
  };
  const failure = error || (!updated && loadError);

  const addressSet = Boolean(status?.addressSet);
  const configured = Boolean(status?.secret?.configured);
  const spec = status?.secret ?? app.connection.secret;
  const done = addressSet && (configured || !spec?.required);
  // Above the app, only an unfinished setup earns space.
  if (setupOnly && done) return null;

  const host = status?.endpoint ? new URL(status.endpoint).host : null;
  return (
    <>
      <details className="app-migration" open={!addressSet}>
        <summary>Service address {host ? `· ${host}` : '· Setup'}</summary>
        <p>
          Where this {app.name} service runs: a public https address, or an http or https address on
          this network. It is reached from your Vela engine.
          {configured
            ? ' Changing the address clears the saved key, and you will save it again for the new one.'
            : ''}
        </p>
        <FormField label="Service address">
          <input
            id={`endpoint-${app.id}`}
            className="connection-input"
            value={endpoint}
            placeholder="https://panel.example.com or http://192.168.1.20:8080"
            disabled={pending || loading}
            onChange={(event) => setEndpoint(event.target.value)}
          />
        </FormField>
        <div className="actions">
          <Button
            size="small"
            pending={pending}
            disabled={loading || !endpoint.trim()}
            onClick={saveAddress}
          >
            Save address
          </Button>
        </div>
        {failure && <p role="alert">{failure.message}</p>}
      </details>
      {/* Remounted when the address changes, so the key form learns that the
          saved key went with the old address. */}
      {addressSet ? (
        <SecretForm key={`${app.id}-${status?.endpoint}`} app={app} setupOnly={setupOnly} />
      ) : null}
    </>
  );
}

export default function AppConnection({ app, setupOnly = false }) {
  if (!app.connection || !app.installed) return null;
  if (app.connection.provider === 'http') {
    if (app.connection.selfHosted)
      return <SelfHostedForm key={app.id} app={app} setupOnly={setupOnly} />;
    return <SecretForm key={app.id} app={app} setupOnly={setupOnly} />;
  }
  return <ConnectionForm key={app.id} app={app} setupOnly={setupOnly} />;
}
