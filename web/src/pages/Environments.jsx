import { Link } from 'react-router-dom';
import { formatBytes } from '../api.js';
import { useApps, useEngine } from '../store.jsx';
import AppIcon from '../components/AppIcon.jsx';

export default function Environments() {
  const { apps } = useApps();
  const { engine, engineError } = useEngine();
  const running = (apps || []).filter((a) => a.running);

  return (
    <div className="page-inner">
      <header>
        <h1 className="page-title">App Environments</h1>
        <p className="page-sub">Apps run locally on your machine, served inside the hub.</p>
      </header>

      <section className="panel engine-card">
        <div className="engine-head">
          <div className="engine-title">
            <span className={`status-dot${engine && !engineError ? ' status-dot-ok' : ' status-dot-bad'}`} />
            <h2>Local Engine</h2>
          </div>
          <span className={`pill${engine && !engineError ? ' pill-ok' : ' pill-bad'}`}>
            {engine && !engineError ? 'Running' : 'Unreachable'}
          </span>
        </div>
        {engineError && <p className="panel-note">Engine status unavailable: {engineError.message}</p>}
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
          <Link className="btn btn-small" to="/apps">Manage</Link>
        </div>
        {running.length === 0 ? (
          <p className="panel-note">Nothing is running right now. Launch an app from Apps or the Library.</p>
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
                <Link className="btn btn-small" to={`/app/${app.id}`}>Open</Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
