import { useEffect, useState } from 'react';
import { api } from '../api.js';

export default function AppActions({ app }) {
  const actionsEnabled = app.capabilities?.includes('actions');
  const [requests, setRequests] = useState([]),
    [events, setEvents] = useState([]),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const refresh = async () => {
    const [status, history] = await Promise.all([
      api.appActions(app.id),
      api.actionHistory(app.id),
    ]);
    setRequests(status.requests);
    setEvents(history.executions);
  };
  useEffect(() => {
    if (!actionsEnabled) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const [status, history] = await Promise.all([
          api.appActions(app.id),
          api.actionHistory(app.id),
        ]);
        if (!cancelled) {
          setRequests(status.requests);
          setEvents(history.executions);
        }
      } catch (failure) {
        if (!cancelled) setError(failure.message);
      }
    };
    poll();
    const timer = setInterval(poll, 3000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [app.id, actionsEnabled]);
  if (!actionsEnabled) return null;
  return (
    <details className="app-migration">
      <summary>App actions &amp; activity</summary>
      {requests.map((item) => (
        <div key={`${item.app}/${item.action}`}>
          <p>
            <strong>{item.title || item.action}</strong> in {item.targetName || item.app}.{' '}
            {item.granted ? 'Allowed.' : 'Not allowed.'}
          </p>
          <p>
            This lets {app.name} create records through this one action. It does not grant access to
            the other app’s raw storage. Updating either app requires a new grant.
          </p>
          <button
            className="btn btn-small"
            disabled={busy || !item.available}
            onClick={async () => {
              setBusy(true);
              setError('');
              try {
                await api.grantAction(
                  app.id,
                  item.app,
                  item.action,
                  !item.granted,
                  item.sourceContract,
                  item.targetContract,
                );
                await refresh();
              } catch (failure) {
                setError(failure.message);
              } finally {
                setBusy(false);
              }
            }}
          >
            {item.granted ? 'Revoke action' : 'Allow action'}
          </button>
          {!item.available && <p>{item.error}. Install or upgrade the target app first.</p>}
        </div>
      ))}
      {error && <p role="alert">{error}</p>}
      <h3>Recent execution</h3>
      {!events.length && <p>No actions have run yet.</p>}
      <ul>
        {events.slice(0, 10).map((event) => (
          <li key={event.id}>
            {event.source_app} → {event.target_app} · {event.action} · {event.status} ·{' '}
            {new Date(event.created_at).toLocaleTimeString()}
            {event.error ? ` · ${event.error}` : ''}
          </li>
        ))}
      </ul>
    </details>
  );
}
