import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { X } from '@phosphor-icons/react';
import { api } from '../api.js';
import { useApps } from '../store.jsx';
import { useDeveloperTools } from '../developer.js';
import AppIcon from './AppIcon.jsx';
import AppPermissions from './AppPermissions.jsx';
import AppConnection from './AppConnection.jsx';
import AppDataMigration from './AppDataMigration.jsx';
import AppDiagnostics from './AppDiagnostics.jsx';
import Button from './ui/Button.jsx';
import Drawer from './ui/Drawer.jsx';

// The management that used to sit above every app: its permissions, its
// connection, an earlier-data import, updates and removal. Opened on request
// from the app's own workspace, so using the app is not preceded by a stack of
// administration panels. Diagnostics join it when Developer tools is on.
export default function AppSettingsDrawer({ app, onClose }) {
  const navigate = useNavigate();
  const { busyIds, runAction, refreshApps } = useApps();
  const developer = useDeveloperTools();
  const [updating, setUpdating] = useState(false);
  const [updateError, setUpdateError] = useState('');
  const closeButton = useRef(null);
  const busy = busyIds.has(app.id) || updating;

  const update = async () => {
    setUpdating(true);
    setUpdateError('');
    try {
      await api.upgrade(app.id);
      await refreshApps();
    } catch (failure) {
      setUpdateError(failure.message);
    } finally {
      setUpdating(false);
    }
  };

  const remove = async () => {
    await runAction(app.id, 'uninstall');
    onClose();
    navigate('/');
  };

  return (
    <Drawer
      open
      onClose={onClose}
      pending={busy}
      initialFocusRef={closeButton}
      aria-label={`${app.name} settings`}
    >
      <div className="drawer-header">
        <div className="drawer-title-row">
          <AppIcon app={app} size={44} />
          <div>
            <h2 className="drawer-name">{app.name}</h2>
            <p className="drawer-meta">App settings</p>
          </div>
        </div>
        <button
          ref={closeButton}
          type="button"
          className="drawer-close"
          disabled={busy}
          onClick={onClose}
          aria-label="Close app settings"
        >
          <X size={20} />
        </button>
      </div>

      <div className="drawer-body">
        <AppPermissions app={app} />
        <AppConnection app={app} />
        <AppDataMigration app={app} />

        <section className="drawer-section">
          <h3 className="drawer-section-title">This app</h3>
          {app.upgradeAvailable && (
            <p className="panel-note">
              A newer build of {app.name} is available. Your existing records and the installed
              package are kept.
            </p>
          )}
          {updateError && (
            <p className="inline-error" role="alert">
              {updateError}
            </p>
          )}
          <div className="actions">
            {app.upgradeAvailable && (
              <Button variant="primary" size="small" disabled={busy} onClick={update}>
                Update app
              </Button>
            )}
            <Button size="small" variant="danger" disabled={busy} onClick={remove}>
              Remove app
            </Button>
          </div>
          <p className="panel-note">
            Removing {app.name} deletes the app and the data it kept on this computer.
          </p>
        </section>

        {developer && <AppDiagnostics app={app} />}
      </div>

      <div className="drawer-footer">
        <div className="actions">
          <Button disabled={busy} onClick={onClose}>
            Done
          </Button>
        </div>
      </div>
    </Drawer>
  );
}
