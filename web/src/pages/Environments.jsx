import { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { formatBytes } from '../api.js';
import { useApps, useEngine } from '../store.jsx';
import { setDeveloperTools, useDeveloperTools } from '../developer.js';
import AppIcon from '../components/AppIcon.jsx';
import Button from '../components/ui/Button.jsx';
import PageHeader from '../components/ui/PageHeader.jsx';
import WorkspacePage from '../components/WorkspacePage.jsx';

// The one System overview: where apps run, what the engine reports, and what
// is running right now. It is a developer surface, so an ordinary visit — or a
// bookmark kept from an earlier release — explains what it is and offers to
// turn the preference on. Visiting never turns it on by itself.
function Locked() {
  const heading = useRef(null);
  useEffect(() => {
    heading.current?.focus();
  }, []);

  return (
    <section className="panel">
      <div className="panel-head">
        <h2 tabIndex={-1} ref={heading}>
          Developer tools are off
        </h2>
      </div>
      <p className="panel-note">
        System shows the engine behind your apps: where they run, what is running now, and where
        their data is kept. Turning developer tools on changes what this browser shows — it does not
        change any app’s permissions, and it does not start or stop anything.
      </p>
      <div className="actions">
        <Button variant="primary" onClick={() => setDeveloperTools(true)}>
          Enable developer tools
        </Button>
        <Link className="btn" to="/">
          Back to Home
        </Link>
      </div>
    </section>
  );
}

export default function Environments() {
  const { apps } = useApps();
  const { engine, engineError } = useEngine();
  const developer = useDeveloperTools();
  const running = (apps || []).filter((a) => a.running);

  return (
    <WorkspacePage>
      <div className="page-inner">
        <PageHeader title="System" description="The engine that runs your apps on this computer." />

        {!developer ? (
          <Locked />
        ) : (
          <>
            <section className="panel engine-card">
              <div className="engine-head">
                <div className="engine-title">
                  <span
                    className={`status-dot${engine && !engineError ? ' status-dot-ok' : ' status-dot-bad'}`}
                  />
                  <h2>Local Engine</h2>
                </div>
                <span className={`pill${engine && !engineError ? ' pill-ok' : ' pill-bad'}`}>
                  {engine && !engineError ? 'Running' : 'Unreachable'}
                </span>
              </div>
              {engineError && (
                <p className="panel-note">Engine status unavailable: {engineError.message}</p>
              )}
              {engine && (
                <dl className="fact-grid">
                  <div className="fact">
                    <dt>Endpoint</dt>
                    <dd className="mono">{engine.endpoint}</dd>
                  </div>
                  <div className="fact">
                    <dt>Applications installed</dt>
                    <dd>{engine.apps_installed ?? 0}</dd>
                  </div>
                  <div className="fact">
                    <dt>Running now</dt>
                    <dd>{engine.apps_running ?? 0}</dd>
                  </div>
                  <div className="fact">
                    <dt>Storage used</dt>
                    <dd>{formatBytes(engine.storage_bytes)}</dd>
                  </div>
                  <div className="fact fact-wide">
                    <dt>Data directory</dt>
                    <dd className="mono">{engine.data_dir}</dd>
                  </div>
                </dl>
              )}
            </section>

            <section className="panel">
              <div className="panel-head">
                <h2>Running apps</h2>
                <Link className="btn btn-small" to="/apps/manage">
                  Manage apps
                </Link>
              </div>
              {running.length === 0 ? (
                <p className="panel-note">
                  Nothing is running right now. Opening an app starts it.
                </p>
              ) : (
                <ul className="mini-list">
                  {running.map((app) => (
                    <li key={app.id}>
                      <AppIcon app={app} size={28} />
                      <span className="mini-list-name">{app.name}</span>
                      <span className="badge badge-running">
                        <span className="pulse-dot" />
                        Running
                      </span>
                      <Link className="btn btn-small" to={`/app/${app.id}`}>
                        Open
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </div>
    </WorkspacePage>
  );
}
