import { useEffect, useRef } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { formatBytes } from '../api.js';
import { useApps, useEngine } from '../store.jsx';
import { setDeveloperTools, useDeveloperTools } from '../developer.js';
import AppIcon from '../components/AppIcon.jsx';
import Button from '../components/ui/Button.jsx';
import PageHeader from '../components/ui/PageHeader.jsx';
import WorkspacePage from '../components/WorkspacePage.jsx';
import LogViewer from '../components/log-viewer/LogViewer.jsx';

// System: where apps run, what the engine reports, and the record of what
// happened — the logs now, errors next to them. It is a developer surface, so
// an ordinary visit — or a bookmark kept from an earlier release — explains
// what it is and offers to turn the preference on. Visiting never turns it on
// by itself.
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
        System shows the engine behind your apps: where they run, what is running now, where their
        data is kept, and the logs they write. Turning developer tools on changes what this browser
        shows — it does not change any app’s permissions, and it does not start or stop anything.
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

const TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'logs', label: 'Logs' },
];

function Overview({ apps, engine, engineError }) {
  const running = (apps || []).filter((a) => a.running);
  return (
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
          <Link className="btn btn-small" to="/library?tab=installed">
            Manage apps
          </Link>
        </div>
        {running.length === 0 ? (
          <p className="panel-note">Nothing is running right now. Opening an app starts it.</p>
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
  );
}

export default function System() {
  const { apps } = useApps();
  const { engine, engineError } = useEngine();
  const developer = useDeveloperTools();
  const [params, setParams] = useSearchParams();
  const tab = TABS.some((t) => t.key === params.get('tab')) ? params.get('tab') : 'overview';

  // The route stays `/environments`; the tab and the open log ride in the
  // query, so a reload and a shared link land on the same place.
  const setTab = (key) => setParams(key === 'overview' ? {} : { tab: key }, { replace: true });
  const selectLog = (name) => setParams({ tab: 'logs', log: name }, { replace: true });

  return (
    <WorkspacePage>
      <div className="page-inner">
        <PageHeader title="System" description="The engine that runs your apps on this computer." />

        {!developer ? (
          <Locked />
        ) : (
          <>
            <div className="seg" role="tablist" aria-label="System sections">
              {TABS.map((t) => (
                <button
                  key={t.key}
                  role="tab"
                  aria-selected={tab === t.key}
                  className={`seg-opt${tab === t.key ? ' seg-opt-active' : ''}`}
                  onClick={() => setTab(t.key)}
                >
                  {t.label}
                </button>
              ))}
            </div>

            {tab === 'overview' && (
              <Overview apps={apps} engine={engine} engineError={engineError} />
            )}
            {tab === 'logs' && (
              <LogViewer selected={params.get('log') || ''} onSelect={selectLog} />
            )}
          </>
        )}
      </div>
    </WorkspacePage>
  );
}
