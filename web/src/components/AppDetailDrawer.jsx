import { useEffect, useRef, useState } from 'react';
import Drawer from './ui/Drawer.jsx';
import ReleaseHistory from './ReleaseHistory.jsx';
import { useNavigate, useLocation } from 'react-router-dom';
import { X } from '@phosphor-icons/react';
import { api, isWebApp, isProcessApp, platformLabel } from '../api.js';
import { useAppStatus, useApps } from '../store.jsx';
import AppIcon from './AppIcon.jsx';
import StatusBadge from './StatusBadge.jsx';
import AddToHomeScreen from './AddToHomeScreen.jsx';

// App detail drawer: description, author, version, category, live status
// (3s poll), log tail for process apps, Add-to-Home-Screen for web apps.
// Open navigates to the embedded view — never to a port.
export default function AppDetailDrawer({ app, busy, onAction, onClose }) {
  const navigate = useNavigate();
  const { refreshApps } = useApps();
  const [upgradeError, setUpgradeError] = useState('');
  const [upgrading, setUpgrading] = useState(false);
  const location = useLocation();
  const [detail, setDetail] = useState(null);
  const { status, statusError } = useAppStatus(app?.id, { enabled: Boolean(app) });
  const logRef = useRef(null);
  const closeButton = useRef(null);
  const appId = app?.id;
  const pending = busy || upgrading;

  useEffect(() => {
    if (!appId) return undefined;
    setDetail(null);
    setUpgradeError('');
    let cancelled = false;
    api
      .getApp(appId)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch(() => {});

    return () => {
      cancelled = true;
    };
  }, [appId]);

  // Keep the log tail pinned to the bottom as new lines arrive.
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [status?.logs]);

  if (!app) return null;

  const webApp = isWebApp(detail ?? app);
  const manifestPlatforms = detail?.manifest?.platforms || null;
  const live = status
    ? { ...app, installed: status.installed, running: status.running, url: status.url }
    : app;

  const openEmbedded = () => {
    onClose();
    navigate(`/app/${app.id}`, { state: { returnTo: location.pathname + location.search } });
  };

  return (
    <Drawer
      open
      onClose={onClose}
      pending={pending}
      initialFocusRef={closeButton}
      aria-label={`${app.name} details`}
    >
      <div className="drawer-header">
        <div className="drawer-title-row">
          <AppIcon app={app} size={52} />
          <div>
            <h2 className="drawer-name">{app.name}</h2>
            <p className="drawer-meta">
              v{app.version}
              {app.category ? ` · ${app.category}` : ''}
              {webApp ? ' · web app' : ''}
            </p>
          </div>
        </div>
        <button
          ref={closeButton}
          type="button"
          className="drawer-close"
          disabled={pending}
          onClick={onClose}
          aria-label="Close details"
        >
          <X size={20} />
        </button>
      </div>

      <div className="drawer-body">
        <div className="drawer-row">
          <StatusBadge app={live} />
          {live.running && (
            <span className="drawer-running-note">Running locally inside the hub</span>
          )}
        </div>

        {!app.supported && <p className="app-row-note">Not supported on this platform</p>}

        <section className="drawer-section">
          <p className="drawer-description">{detail?.manifest?.description || app.description}</p>
          <dl className="drawer-facts">
            {app.author && (
              <>
                <dt>Author</dt>
                <dd>{app.author}</dd>
              </>
            )}
            <dt>App ID</dt>
            <dd className="mono">{app.id}</dd>
            <dt>Access</dt>
            <dd>
              {isProcessApp(app)
                ? 'Trusted native code · runs as your user'
                : app.schemaVersion === 2
                  ? app.capabilities?.includes('storage')
                    ? 'Own persistent app data'
                    : 'No engine data access'
                  : 'Trusted legacy app'}
            </dd>
            {webApp ? (
              <>
                <dt>Type</dt>
                <dd>
                  {app.schemaVersion === 2 ? 'Static app — isolated view' : 'Legacy web app (PWA)'}
                </dd>
                <dt>Installed</dt>
                <dd>{live.installed ? 'Yes' : 'No'}</dd>
              </>
            ) : (
              <>
                {status?.pid != null && (
                  <>
                    <dt>PID</dt>
                    <dd className="mono">{status.pid}</dd>
                  </>
                )}
                {status?.started_at && (
                  <>
                    <dt>Started</dt>
                    <dd>{new Date(status.started_at).toLocaleString()}</dd>
                  </>
                )}
              </>
            )}
          </dl>
        </section>

        {webApp && app.schemaVersion !== 2 && <AddToHomeScreen appName={app.name} />}

        {manifestPlatforms && (
          <section className="drawer-section">
            <h3 className="drawer-section-title">Platforms</h3>
            <ul className="drawer-platforms">
              {Object.entries(manifestPlatforms).map(([key, cfg]) => (
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
          </section>
        )}

        <ReleaseHistory app={app} />
        {isProcessApp(app) && (
          <section className="drawer-section">
            <h3 className="drawer-section-title">Logs</h3>
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
          </section>
        )}
      </div>

      <div className="drawer-footer">
        {app.upgradeAvailable && (
          <p>
            A new SDK version is available. Your earlier browser records and installed package are
            retained.
          </p>
        )}
        {upgradeError && <p role="alert">{upgradeError}</p>}
        <div className="actions">
          {app.upgradeAvailable && (
            <button
              className="btn btn-primary"
              disabled={pending}
              onClick={async () => {
                setUpgrading(true);
                try {
                  await api.upgrade(app.id);
                  await refreshApps();
                } catch (failure) {
                  setUpgradeError(failure.message);
                } finally {
                  setUpgrading(false);
                }
              }}
            >
              Upgrade to SDK version
            </button>
          )}
          {app.supported && live.installed && (
            <button className="btn btn-primary" disabled={pending} onClick={openEmbedded}>
              Open
            </button>
          )}
          {app.supported && !live.installed && (
            <button
              className="btn btn-primary"
              disabled={pending}
              onClick={() => onAction(app.id, 'install')}
            >
              Install
            </button>
          )}
          {app.supported && live.installed && !live.running && (
            <button className="btn" disabled={pending} onClick={() => onAction(app.id, 'launch')}>
              Launch
            </button>
          )}
          {app.supported && live.running && isProcessApp(app) && (
            <button className="btn" disabled={pending} onClick={() => onAction(app.id, 'stop')}>
              Stop
            </button>
          )}
          {app.supported && live.installed && (
            <button
              className="btn btn-danger"
              disabled={pending}
              onClick={() => onAction(app.id, 'uninstall')}
            >
              Uninstall
            </button>
          )}
        </div>
      </div>
    </Drawer>
  );
}
