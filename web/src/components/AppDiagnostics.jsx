import { useEffect, useRef, useState } from 'react';
import { api, isProcessApp, platformLabel } from '../api.js';
import { useApps, useAppStatus } from '../store.jsx';
import Button from './ui/Button.jsx';

// Everything technical about one app, in one collapsed place: identifiers, the
// process it runs, its logs, what has executed through it, and the manual
// lifecycle controls. Nothing here loads until the section is opened, so a
// closed diagnostics block costs an ordinary user nothing.
//
// Rendering this is the caller's decision and depends on the Developer tools
// preference. The data it shows is the same data the backend already exposes;
// opening it grants nothing.
export default function AppDiagnostics({ app }) {
  const [open, setOpen] = useState(false);
  const { busyIds, runAction } = useApps();
  const { status, statusError } = useAppStatus(app?.id, { enabled: open && Boolean(app) });
  const [manifest, setManifest] = useState(null);
  const [events, setEvents] = useState(null);
  const [historyError, setHistoryError] = useState('');
  const logRef = useRef(null);
  const appId = app?.id;
  const actions = Boolean(app?.capabilities?.includes('actions'));

  useEffect(() => {
    if (!open || !appId) return undefined;
    let cancelled = false;
    api
      .getApp(appId)
      .then((data) => {
        if (!cancelled) setManifest(data.manifest || null);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [open, appId]);

  useEffect(() => {
    if (!open || !appId || !actions) return undefined;
    let cancelled = false;
    const poll = async () => {
      try {
        const history = await api.actionHistory(appId);
        if (!cancelled) {
          setEvents(history.executions);
          setHistoryError('');
        }
      } catch (failure) {
        if (!cancelled) setHistoryError(failure.message);
      }
    };
    poll();
    const timer = setInterval(poll, 5000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [open, appId, actions]);

  // Keep the log tail pinned to the bottom as new lines arrive.
  useEffect(() => {
    const element = logRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [status?.logs]);

  if (!app) return null;
  const busy = busyIds.has(app.id);
  const process = isProcessApp(app);
  const platforms = manifest?.platforms || null;

  return (
    <details className="app-diagnostics" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>Diagnostics</summary>
      <dl className="drawer-facts">
        <dt>App ID</dt>
        <dd className="mono">{app.id}</dd>
        <dt>Runtime</dt>
        <dd className="mono">{app.runtime || '—'}</dd>
        {app.schemaVersion != null && (
          <>
            <dt>Schema</dt>
            <dd className="mono">v{app.schemaVersion}</dd>
          </>
        )}
        {process && (
          <>
            <dt>Process</dt>
            <dd className="mono">{status?.pid != null ? `pid ${status.pid}` : 'not running'}</dd>
            {status?.started_at && (
              <>
                <dt>Started</dt>
                <dd>{new Date(status.started_at).toLocaleString()}</dd>
              </>
            )}
          </>
        )}
      </dl>

      {platforms && (
        <>
          <h4 className="drawer-section-title">Platforms</h4>
          <ul className="drawer-platforms">
            {Object.entries(platforms).map(([key, cfg]) => (
              <li key={key} className={cfg ? '' : 'platform-unsupported'}>
                <span className="platform-name">{platformLabel(key)}</span>
                {cfg ? (
                  <span className="mono platform-detail">
                    {key === 'web'
                      ? `entry ${cfg.entry || 'index.html'}`
                      : `${cfg.port != null ? `port ${cfg.port}` : 'no port'}${cfg.run ? ` · ${cfg.run}` : ''}`}
                  </span>
                ) : (
                  <span className="platform-detail">unsupported</span>
                )}
              </li>
            ))}
          </ul>
        </>
      )}

      {process && (
        <>
          <h4 className="drawer-section-title">Logs</h4>
          {statusError ? (
            <p className="drawer-log-empty">Status unavailable: {statusError}</p>
          ) : status?.logs ? (
            <pre ref={logRef} className="drawer-log mono">
              {status.logs}
            </pre>
          ) : (
            <p className="drawer-log-empty">
              {status?.running
                ? 'No log output yet.'
                : 'Logs appear here while the app is running.'}
            </p>
          )}
          <h4 className="drawer-section-title">Process controls</h4>
          <div className="actions">
            <Button
              size="small"
              disabled={busy || Boolean(status?.running)}
              onClick={() => runAction(app.id, 'launch')}
            >
              Start
            </Button>
            <Button
              size="small"
              disabled={busy || !status?.running}
              onClick={() => runAction(app.id, 'stop')}
            >
              Stop
            </Button>
          </div>
        </>
      )}

      {actions && (
        <>
          <h4 className="drawer-section-title">Activity</h4>
          {historyError && <p className="drawer-log-empty">{historyError}</p>}
          {events && events.length === 0 && (
            <p className="drawer-log-empty">Nothing has run yet.</p>
          )}
          {events && events.length > 0 && (
            <ul className="app-activity mono">
              {events.slice(0, 10).map((event) => (
                <li key={event.id}>
                  {event.source_app} → {event.target_app} · {event.action} · {event.status} ·{' '}
                  {new Date(event.created_at).toLocaleTimeString()}
                  {event.error ? ` · ${event.error}` : ''}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </details>
  );
}
