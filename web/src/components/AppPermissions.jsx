import { useCallback, useEffect, useState } from 'react';
import { api } from '../api.js';
import Button from './ui/Button.jsx';

// Permission decisions for one app, separate from the technical record of what
// has run. This is an everyday screen: it names the app asking, the action it
// wants, the app the action happens in, and what allowing it does and does not
// give away. It is never hidden by the Developer tools preference, because a
// grant is an authorization decision and that preference only changes display.
export function useActionRequests(app) {
  const enabled = Boolean(app?.capabilities?.includes('actions'));
  const id = app?.id;
  const [requests, setRequests] = useState([]);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    if (!enabled || !id) return;
    try {
      const status = await api.appActions(id);
      setRequests(status.requests);
      setError('');
    } catch (failure) {
      setError(failure.message);
    }
  }, [enabled, id]);

  useEffect(() => {
    if (!enabled || !id) return undefined;
    let cancelled = false;
    const poll = async () => {
      try {
        const status = await api.appActions(id);
        if (!cancelled) {
          setRequests(status.requests);
          setError('');
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
  }, [enabled, id]);

  return { enabled, requests, error, refresh };
}

export default function AppPermissions({ app }) {
  const { enabled, requests, error, refresh } = useActionRequests(app);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState('');

  if (!enabled) return null;

  const decide = async (item) => {
    setBusy(true);
    setFailure('');
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
    } catch (problem) {
      setFailure(problem.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="app-permissions">
      <h3 className="drawer-section-title">Permissions</h3>
      {requests.length === 0 && (
        <p className="panel-note">{app.name} has not asked to do anything in another app.</p>
      )}
      {requests.map((item) => (
        <div className="app-permission" key={`${item.app}/${item.action}`}>
          <p className="app-permission-ask">
            <strong>{app.name}</strong> wants to {(item.title || item.action).toLowerCase()} in{' '}
            <strong>{item.targetName || item.app}</strong>.
          </p>
          <p className="app-permission-scope">
            Allowing this lets {app.name} create records through that one action. It does not give{' '}
            {app.name} access to {item.targetName || item.app}’s stored data, and updating either
            app asks you again.
          </p>
          {!item.granted && (
            <p className="app-permission-scope">
              Until you allow it, {app.name} cannot complete this action and will say so.
            </p>
          )}
          <div className="actions">
            <Button
              size="small"
              variant={item.granted ? undefined : 'primary'}
              disabled={busy || !item.available}
              onClick={() => decide(item)}
            >
              {item.granted ? 'Stop allowing' : 'Allow'}
            </Button>
          </div>
          {!item.available && (
            <p className="panel-note">{item.error}. Install or update the other app first.</p>
          )}
        </div>
      ))}
      {(failure || error) && (
        <p className="inline-error" role="alert">
          {failure || error}
        </p>
      )}
    </section>
  );
}

// A decision that is still waiting has to be discoverable while using the app,
// without a blocking dialog on every visit. One line, with the full
// explanation and controls a click away in the app's settings.
export function PermissionNotice({ app, onReview }) {
  const { enabled, requests } = useActionRequests(app);
  const waiting = requests.filter((item) => !item.granted && item.available);
  if (!enabled || waiting.length === 0) return null;
  const [first] = waiting;
  return (
    <div className="app-notice" role="status">
      <p>
        {app.name} needs your permission to {(first.title || first.action).toLowerCase()} in{' '}
        {first.targetName || first.app}
        {waiting.length > 1 ? `, and ${waiting.length - 1} more` : ''}.
      </p>
      <Button size="small" variant="primary" onClick={onReview}>
        Review
      </Button>
    </div>
  );
}
