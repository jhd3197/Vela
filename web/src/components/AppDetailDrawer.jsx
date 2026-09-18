import { useEffect, useRef, useState } from 'react';
import Drawer from './ui/Drawer.jsx';
import ReleaseHistory from './ReleaseHistory.jsx';
import { useLocation } from 'react-router-dom';
import { X } from '@phosphor-icons/react';
import { api, isWebApp, isProcessApp } from '../api.js';
import { useAppStatus, useApps } from '../store.jsx';
import { useDeveloperTools } from '../developer.js';
import useOpenApp from '../desktops/useOpenApp.js';
import AppIcon from './AppIcon.jsx';
import StatusBadge from './StatusBadge.jsx';
import AddToHomeScreen from './AddToHomeScreen.jsx';
import AppPermissions from './AppPermissions.jsx';
import AppDiagnostics from './AppDiagnostics.jsx';
import ConnectedAppForm from './ConnectedAppForm.jsx';

// App detail drawer: what the app is, who wrote it, what it can reach, what it
// may do in other apps, and the actions that apply to it. Identifiers, the
// process, its logs and its execution history are diagnostics, so they load on
// request under Developer tools. Open goes through the shared Open action —
// never to a port, and never starting anything the user did not ask for.
export default function AppDetailDrawer(props) {
  if (props.app?.kind === 'connected-web')
    return <ConnectedAppDetail key={props.app.id} {...props} />;
  return <PackageAppDetail {...props} />;
}

function ConnectedAppDetail({ app, onClose }) {
  const { openApp } = useApps();
  const location = useLocation();
  const [editing, setEditing] = useState(false);
  return (
    <Drawer open onClose={onClose} aria-label={`${app.name} details`}>
      <div className="drawer-header">
        <div className="drawer-title-row">
          <AppIcon app={app} size={52} />
          <div>
            <h2 className="drawer-name">{app.name}</h2>
            <p className="drawer-meta">Connected web app</p>
          </div>
        </div>
        <button className="drawer-close" onClick={onClose} aria-label="Close details">
          <X size={20} />
        </button>
      </div>
      <div className="drawer-body">
        <section className="drawer-section">
          <p className="drawer-description">{app.description}</p>
          <dl className="drawer-facts">
            <dt>Address</dt>
            <dd className="connected-app-address">{app.url}</dd>
            <dt>Access</dt>
            <dd>Own website and sign-in. No Vela app data access.</dd>
            <dt>Availability</dt>
            <dd>Managed by the service. Vela does not monitor or start it.</dd>
          </dl>
        </section>
      </div>
      <div className="drawer-footer">
        <div className="actions">
          <button
            className="btn btn-primary"
            onClick={() => {
              onClose();
              openApp(app.id, { returnTo: location.pathname + location.search });
            }}
          >
            Open
          </button>
          <button className="btn" onClick={() => setEditing(true)}>
            Edit connection
          </button>
        </div>
      </div>
      {editing && (
        <ConnectedAppForm
          app={app}
          onClose={() => {
            setEditing(false);
            onClose();
          }}
        />
      )}
    </Drawer>
  );
}

function PackageAppDetail({ app, busy, onAction, onClose }) {
  const { openingId, refreshApps } = useApps();
  const openApp = useOpenApp();
  const developer = useDeveloperTools();
  const [upgradeError, setUpgradeError] = useState('');
  const [upgrading, setUpgrading] = useState(false);
  const location = useLocation();
  const [detail, setDetail] = useState(null);
  const { status } = useAppStatus(app?.id, { enabled: Boolean(app) });
  const closeButton = useRef(null);
  const appId = app?.id;
  const pending = busy || upgrading || openingId === appId;

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

  if (!app) return null;

  const webApp = isWebApp(detail ?? app);
  const live = status
    ? { ...app, installed: status.installed, running: status.running, url: status.url }
    : app;

  const openEmbedded = () => {
    onClose();
    openApp(app.id, { returnTo: location.pathname + location.search });
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
          {live.running && developer && (
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
            {webApp && (
              <>
                <dt>Type</dt>
                <dd>
                  {app.schemaVersion === 2 ? 'Static app — isolated view' : 'Legacy web app (PWA)'}
                </dd>
              </>
            )}
          </dl>
        </section>

        {webApp && app.schemaVersion !== 2 && <AddToHomeScreen appName={app.name} />}

        <ReleaseHistory app={app} />
        {live.installed && <AppPermissions app={app} />}
        {developer && live.installed && <AppDiagnostics app={app} />}
      </div>

      <div className="drawer-footer">
        {app.upgradeAvailable && (
          <p>
            A newer build of {app.name} is available. Your existing records and the installed
            package are kept.
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
              Update app
            </button>
          )}
          {app.supported && live.installed && (
            <button className="btn btn-primary" disabled={pending} onClick={openEmbedded}>
              {openingId === app.id ? 'Opening…' : 'Open'}
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
          {app.supported && live.installed && (
            <button
              className="btn btn-danger"
              disabled={pending}
              onClick={() => onAction(app.id, 'uninstall')}
            >
              Remove
            </button>
          )}
        </div>
      </div>
    </Drawer>
  );
}
