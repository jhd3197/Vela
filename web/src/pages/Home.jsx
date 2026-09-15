import LoadingState from '../components/ui/LoadingState.jsx';
import { Link } from 'react-router-dom';
import {
  ArrowRight,
  CaretRight,
  CloudSlash,
  HardDrives,
  Plus,
  ShieldCheck,
} from '@phosphor-icons/react';
import { formatBytes, platformLabel } from '../api.js';
import { useApps, useEngine } from '../store.jsx';
import AppIcon from '../components/AppIcon.jsx';

function greeting() {
  const h = new Date().getHours();
  if (h < 5) return 'Good night';
  if (h < 12) return 'Good morning';
  if (h < 18) return 'Good afternoon';
  return 'Good evening';
}

// Home dashboard per the Nocturne prototype: greeting + activity pulse,
// "Your apps" tile grid, system widgets, and a getting-started banner while
// there are still apps left to install.
export default function Home() {
  const { apps, platform } = useApps();
  const { engine } = useEngine();

  const installed = (apps || []).filter((a) => a.installed);
  const available = (apps || []).filter((a) => !a.installed && a.supported);
  const running = installed.filter((a) => a.running);

  const today = new Date().toLocaleDateString(undefined, {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
  });

  return (
    <div className="page-inner">
      <header className="home-hero">
        <div>
          <h1 className="home-greeting">{greeting()}</h1>
          <p className="home-meta">
            {today} · {installed.length} {installed.length === 1 ? 'app' : 'apps'} installed
          </p>
        </div>
        {running.length > 0 && (
          <div className="activity-pulse">
            <span className="activity-pulse-label">
              {running.length === 1
                ? `${running[0].name} · running`
                : `${running.length} apps running`}
            </span>
            <span className="activity-pulse-bar" aria-hidden="true" />
          </div>
        )}
      </header>

      {apps === null && <LoadingState>Loading apps…</LoadingState>}

      {apps !== null && (
        <section style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div className="page-head-row" style={{ alignItems: 'baseline' }}>
            <h2 className="section-head">Your apps</h2>
            <Link to="/apps" style={{ fontSize: 12, textDecoration: 'none' }}>
              Manage
            </Link>
          </div>
          {installed.length === 0 ? (
            <div className="state-block">
              <div className="empty-mark" aria-hidden="true">
                <img
                  src="/vela-mark.png"
                  alt=""
                  width={44}
                  height={44}
                  style={{ margin: '0 auto' }}
                />
              </div>
              <h2>Nothing installed yet</h2>
              <p>
                Your hub is empty. Browse the Library to install your first app — it will run right
                here, on your machine.
              </p>
              <Link className="btn btn-primary" to="/library">
                Browse Library
              </Link>
            </div>
          ) : (
            <div className="tiles-grid">
              {installed.map((app) => (
                <Link
                  key={app.id}
                  to={`/app/${app.id}`}
                  state={{ returnTo: '/' }}
                  className="tile-card"
                >
                  <AppIcon app={app} size={38} />
                  <span className="tile-card-text">
                    <span className="tile-card-name">{app.name}</span>
                    <span
                      className={`tile-card-meta${app.running ? ' tile-card-meta-running' : ''}`}
                    >
                      {app.kind === 'connected-web'
                        ? 'Web app'
                        : app.running
                          ? 'Running'
                          : `v${app.version}`}
                      {app.category ? ` · ${app.category}` : ''}
                    </span>
                  </span>
                </Link>
              ))}
              <Link to="/library" className="tile-add">
                <Plus size={20} />
                <span className="tile-add-name">Add an app</span>
                <span className="tile-add-sub">From the Library</span>
              </Link>
            </div>
          )}
        </section>
      )}

      {apps !== null && installed.length > 0 && (
        <section className="widget-grid">
          <article className="widget">
            <div className="widget-head">
              <span className="card-kicker">System</span>
              <span className={`status-dot${engine ? ' status-dot-ok' : ' status-dot-bad'}`} />
            </div>
            <div className="widget-value">
              <span className="widget-value-num">
                {engine ? formatBytes(engine.storage_bytes) : '—'}
              </span>
              <span className="widget-value-unit">stored locally</span>
            </div>
            <div className="widget-rows">
              <span className="widget-row">
                <span className="widget-row-label">
                  <ShieldCheck size={15} />
                  Engine
                </span>
                <span className="widget-row-value">{engine ? 'Running' : 'Unreachable'}</span>
              </span>
              <span className="widget-row">
                <span className="widget-row-label">
                  <CloudSlash size={15} />
                  Sync
                </span>
                <span className="widget-row-value">All data local</span>
              </span>
              <span className="widget-row">
                <span className="widget-row-label">
                  <HardDrives size={15} />
                  Platform
                </span>
                <span className="widget-row-value">
                  {platform ? platformLabel(platform.current) : '—'}
                </span>
              </span>
            </div>
          </article>

          <article className="widget">
            <div className="widget-head">
              <span className="card-kicker">Running now</span>
              <Link to="/apps" style={{ fontSize: 12, textDecoration: 'none' }}>
                All apps
              </Link>
            </div>
            {running.length === 0 ? (
              <p className="panel-note">Nothing is running. Open an app and it shows up here.</p>
            ) : (
              <div className="widget-rows" style={{ borderTop: 'none', paddingTop: 0 }}>
                {running.map((app) => (
                  <Link
                    key={app.id}
                    to={`/app/${app.id}`}
                    state={{ returnTo: '/' }}
                    className="widget-row"
                    style={{ textDecoration: 'none', color: 'inherit' }}
                  >
                    <span className="widget-row-label">
                      <AppIcon app={app} size={22} />
                      {app.name}
                    </span>
                    <CaretRight size={14} className="caret" />
                  </Link>
                ))}
              </div>
            )}
          </article>
        </section>
      )}

      {available.length > 0 && (
        <section className="gs-banner">
          <div className="gs-banner-text">
            <h2 className="gs-banner-title">Fill out your Vela</h2>
            <p className="gs-banner-sub">
              {available
                .map((a) => a.name)
                .slice(0, 4)
                .join(', ')}
              {available.length > 4 ? ` and ${available.length - 4} more` : ''}{' '}
              {available.length === 1 ? 'is' : 'are'} ready to install from the Library.
            </p>
          </div>
          <div className="gs-banner-actions">
            <Link className="btn btn-primary" to="/library">
              Open Library
              <ArrowRight size={15} />
            </Link>
          </div>
        </section>
      )}

      <footer className="home-status">
        <span className={`status-dot${engine ? ' status-dot-ok' : ''}`} />
        {engine
          ? `All systems ready · Running locally · ${engine.apps_running ?? 0} running`
          : 'Connecting to local engine…'}
      </footer>
    </div>
  );
}
