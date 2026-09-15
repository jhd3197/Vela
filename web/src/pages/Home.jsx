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
import WorkspacePage from '../components/WorkspacePage.jsx';

function greeting() {
  const h = new Date().getHours();
  if (h < 5) return 'Good night';
  if (h < 12) return 'Good morning';
  if (h < 18) return 'Good afternoon';
  return 'Good evening';
}

// The one secondary line under each app name. It only reports what the hub
// actually knows: a connection, a running process, or the installed version.
// Per-app summaries would need an app-provided source that does not exist yet.
function appDetail(app) {
  if (app.kind === 'connected-web') return 'Connected web app';
  if (app.running) return 'Running locally';
  if (!app.supported) return 'Not supported on this platform';
  return (
    [app.version && `v${app.version}`, app.category].filter(Boolean).join(' · ') || 'Installed'
  );
}

function TileSkeleton() {
  return (
    <div className="tile-card tile-card-skeleton" aria-hidden="true">
      <span className="skeleton skeleton-icon" />
      <span className="tile-card-text">
        <span className="skeleton skeleton-line" />
        <span className="skeleton skeleton-line skeleton-line-short" />
      </span>
    </div>
  );
}

// Home per the rail reference: greeting and a factual status line, the app
// grid with an Add tile, then widgets built from real engine data.
export default function Home() {
  const { apps, platform, busyIds } = useApps();
  const { engine } = useEngine();

  const loading = apps === null;
  const installed = (apps || []).filter((a) => a.installed);
  const available = (apps || []).filter((a) => !a.installed && a.supported);
  const running = installed.filter((a) => a.running);
  // Only a lifecycle action in flight is real activity; a running app is a
  // state, not progress, so it never animates a progress line.
  const workingOn = installed.find((app) => busyIds.has(app.id));

  const today = new Date().toLocaleDateString(undefined, {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
  });
  const status = [
    today,
    engine?.version && `Vela v${engine.version}`,
    !loading &&
      `${installed.length} ${installed.length === 1 ? 'app' : 'apps'} installed${
        running.length ? `, ${running.length} running` : ''
      }`,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <WorkspacePage>
      <div className="page-inner">
        <header className="home-hero">
          <div>
            <h1 className="home-greeting">{greeting()}</h1>
            <p className="home-meta">{status}</p>
          </div>
          {workingOn && (
            <div className="activity-pulse" role="status">
              <span className="activity-pulse-label">{workingOn.name} · working…</span>
              <span className="activity-pulse-bar" aria-hidden="true" />
            </div>
          )}
        </header>

        <section className="home-section">
          <div className="page-head-row home-section-head">
            <h2 className="section-head">Your apps</h2>
            <Link to="/apps" className="home-section-link">
              Manage
            </Link>
          </div>
          {loading ? (
            <div className="tiles-grid">
              <p className="sr-only" role="status">
                Loading apps…
              </p>
              <TileSkeleton />
              <TileSkeleton />
              <TileSkeleton />
            </div>
          ) : installed.length === 0 ? (
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
                Your server is empty. Browse the Library to install your first app — it will run
                right here, on your machine.
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
                  <AppIcon app={app} size={40} />
                  <span className="tile-card-text">
                    <span className="tile-card-name">{app.name}</span>
                    <span
                      className={`tile-card-meta${app.running ? ' tile-card-meta-running' : ''}`}
                    >
                      {appDetail(app)}
                    </span>
                  </span>
                </Link>
              ))}
              <Link to="/library" className="tile-add">
                <Plus size={20} aria-hidden="true" />
                <span className="tile-add-name">Add an app</span>
                <span className="tile-add-sub">From the Library</span>
              </Link>
            </div>
          )}
        </section>

        {!loading && installed.length > 0 && (
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
                    <ShieldCheck size={15} aria-hidden="true" />
                    Engine
                  </span>
                  <span className="widget-row-value">{engine ? 'Running' : 'Unreachable'}</span>
                </span>
                <span className="widget-row">
                  <span className="widget-row-label">
                    <CloudSlash size={15} aria-hidden="true" />
                    Sync
                  </span>
                  <span className="widget-row-value">All data local</span>
                </span>
                <span className="widget-row">
                  <span className="widget-row-label">
                    <HardDrives size={15} aria-hidden="true" />
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
                <Link to="/apps" className="home-section-link">
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
                      className="widget-row widget-row-link"
                    >
                      <span className="widget-row-label">
                        <AppIcon app={app} size={22} />
                        {app.name}
                      </span>
                      <CaretRight size={14} className="caret" aria-hidden="true" />
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
                <ArrowRight size={15} aria-hidden="true" />
              </Link>
            </div>
          </section>
        )}
      </div>
    </WorkspacePage>
  );
}
