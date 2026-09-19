import { useCallback, useEffect, useState } from 'react';
import { X } from '@phosphor-icons/react';
import { api, formatBytes, managedState, relTime } from '../api.js';
import { useAsyncAction } from '../hooks/useAsyncAction.js';
import { useConfirm } from '../hooks/useConfirm.js';
import Drawer from './ui/Drawer.jsx';
import Button from './ui/Button.jsx';

// Everything a person does to a managed web app that is not "use it": start and
// stop it, decide whether it comes back with Vela, take and restore a backup,
// go back to the previous version, and remove it.
//
// Two things this panel is careful about. Removing keeps the app's data and
// says where it went, because "uninstall" should not be how somebody loses
// their notes. And erasing that data is a separate action with its own
// confirmation, which names the folder and its size, because it is the one
// button here that cannot be undone.
export default function ManagedAppPanel({ app, onClose, onChanged }) {
  const confirm = useConfirm();
  const { run, pending, error } = useAsyncAction();
  const [detail, setDetail] = useState(null);
  const [note, setNote] = useState('');

  const load = useCallback(async () => {
    try {
      setDetail(await api.managedApp(app.id, { cache: 'no-store' }));
    } catch {
      setDetail(null);
    }
  }, [app.id]);

  useEffect(() => {
    load();
  }, [load]);

  const after = async () => {
    await load();
    onChanged?.();
  };

  const act = (action) =>
    run(async () => {
      const result = await action();
      await after();
      return result;
    });

  const managed = detail?.managed || app.managed || {};
  const state = managedState(detail || app);
  const snapshots = managed.snapshots || [];
  const releases = managed.releases || [];
  const previous = releases.find((release) => !release.active);
  const data = managed.data || {};

  return (
    <Drawer open onClose={onClose} aria-label={`${app.name} settings`}>
      <div className="drawer-header">
        <div className="drawer-title-row">
          <div>
            <h2 className="drawer-name">{app.name}</h2>
            <p className="drawer-meta">Managed web app · v{detail?.version || app.version}</p>
          </div>
        </div>
        <button className="drawer-close" onClick={onClose} aria-label="Close settings">
          <X size={20} />
        </button>
      </div>

      <div className="drawer-body">
        {error && (
          <p className="form-error" role="alert">
            {error.message}
          </p>
        )}

        <section className="drawer-section">
          <h3 className="drawer-section-title">Service</h3>
          <p className="managed-note">
            <span className={`badge badge-managed badge-${state.tone}`}>{state.label}</span>{' '}
            {state.detail || 'Vela starts and stops this app for you.'}
          </p>
          <div className="actions">
            {state.tone === 'good' ? (
              <Button onClick={() => act(() => api.stopManaged(app.id))} disabled={pending}>
                Stop
              </Button>
            ) : (
              <Button
                variant="primary"
                onClick={() => act(() => api.startManaged(app.id))}
                disabled={pending || state.busy}
              >
                Start
              </Button>
            )}
          </div>
          <label className="managed-check">
            <input
              type="checkbox"
              checked={Boolean(managed.startWithVela)}
              disabled={pending}
              onChange={(event) => act(() => api.setManagedStartup(app.id, event.target.checked))}
            />
            <span>
              Start with Vela
              <small>
                When this is on, Vela starts the app again after a restart — unless you stopped it
                yourself, which it remembers.
              </small>
            </span>
          </label>
        </section>

        <section className="drawer-section">
          <h3 className="drawer-section-title">Address</h3>
          <dl className="drawer-facts managed-address">
            {(managed.addresses || []).map((address) => [
              <dt key={`${address.url}-label`}>{address.scope}</dt>,
              <dd key={address.url}>
                <code>{address.url}</code>
                {address.requires && <small>Needs {address.requires}.</small>}
              </dd>,
            ])}
          </dl>
        </section>

        <section className="drawer-section">
          <h3 className="drawer-section-title">What this app can do</h3>
          <p className="managed-note">{managed.trust?.summary}</p>
          <dl className="drawer-facts">
            <dt>Source</dt>
            <dd>
              {managed.source?.upstream ? <code>{managed.source.upstream}</code> : 'Local package'}
            </dd>
            <dt>Licence</dt>
            <dd>{managed.source?.license || 'Not stated'}</dd>
            <dt>Build</dt>
            <dd>
              {managed.artifact
                ? `${managed.artifact.os} ${managed.artifact.arch}`
                : managed.target}
            </dd>
            <dt>Vela access</dt>
            <dd>None. Installing this app grants no access to your Vela data.</dd>
          </dl>
        </section>

        <section className="drawer-section">
          <h3 className="drawer-section-title">Data</h3>
          <p className="managed-note">
            {data.describe || 'This app keeps its own files.'}{' '}
            {data.size != null && <>Currently {formatBytes(data.size)}.</>}
          </p>
          <div className="managed-backup-row">
            <input
              placeholder="What is this backup for?"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              aria-label="Backup note"
            />
            <Button
              onClick={() =>
                act(async () => {
                  const result = await api.backUpManaged(app.id, note);
                  setNote('');
                  return result;
                })
              }
              disabled={pending}
            >
              Back up now
            </Button>
          </div>
          {snapshots.length === 0 ? (
            <p className="managed-note">No backups yet.</p>
          ) : (
            <ul className="managed-snapshots">
              {snapshots.map((snapshot) => (
                <li key={snapshot.id}>
                  <div>
                    <strong>{snapshot.note || 'Backup'}</strong>
                    <small>
                      {relTime(snapshot.createdAt)} · v{snapshot.version} ·{' '}
                      {formatBytes(snapshot.size)} · {snapshot.files} files
                    </small>
                  </div>
                  <Button
                    onClick={async () => {
                      const ok = await confirm({
                        title: `Restore ${app.name} to this backup?`,
                        message:
                          'Anything added since this backup was taken is replaced. Vela takes a copy of the current data first, so this can be undone.',
                        confirmText: 'Restore',
                      });
                      if (ok) act(() => api.restoreManaged(app.id, snapshot.id));
                    }}
                    disabled={pending}
                  >
                    Restore
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </section>

        {previous && (
          <section className="drawer-section">
            <h3 className="drawer-section-title">Versions</h3>
            <p className="managed-note">
              Running v{detail?.version || app.version}. The previous version, v{previous.version},
              is still here.
            </p>
            <div className="actions">
              <Button
                onClick={async () => {
                  const ok = await confirm({
                    title: `Go back to v${previous.version}?`,
                    message: `Vela puts back the app's files and the data as they were when v${previous.version} was running. Anything written since is replaced, and a copy of the current data is kept first.`,
                    confirmText: 'Go back',
                  });
                  if (ok) act(() => api.rollbackManaged(app.id, previous.id));
                }}
                disabled={pending}
              >
                Go back to v{previous.version}
              </Button>
            </div>
          </section>
        )}

        <section className="drawer-section">
          <h3 className="drawer-section-title">Remove</h3>
          <p className="managed-note">
            Removing {app.name} stops it and deletes its program. Its own data stays on this
            computer so you can install it again and pick up where you left off.
          </p>
          <div className="actions">
            <Button
              onClick={async () => {
                const ok = await confirm({
                  title: `Remove ${app.name}?`,
                  message: `The app's program is deleted. Its data stays in ${data.path || 'its own folder'} unless you erase it separately.`,
                  confirmText: 'Remove',
                });
                if (ok) {
                  await act(() => api.removeManaged(app.id));
                  onClose();
                }
              }}
              disabled={pending}
            >
              Remove app
            </Button>
            <Button
              variant="danger"
              onClick={async () => {
                const ok = await confirm({
                  title: `Erase ${app.name}'s data?`,
                  message: `This deletes ${formatBytes(data.size || 0)} in ${data.path || "the app's folder"}, including its accounts and anything you saved in it. Backups you have taken are kept. This cannot be undone.`,
                  confirmText: 'Erase data',
                });
                if (ok) act(() => api.eraseManagedData(app.id));
              }}
              disabled={pending}
            >
              Erase data
            </Button>
          </div>
        </section>
      </div>
    </Drawer>
  );
}
