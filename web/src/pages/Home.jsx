import { Link } from 'react-router-dom';
import { Plus } from '@phosphor-icons/react';
import { useApps } from '../store.jsx';
import AppIcon from '../components/AppIcon.jsx';
import WorkspacePage from '../components/WorkspacePage.jsx';

function greeting() {
  const h = new Date().getHours();
  if (h < 5) return 'Good night';
  if (h < 12) return 'Good morning';
  if (h < 18) return 'Good afternoon';
  return 'Good evening';
}

// The one secondary line under an app name. It says what the app is for, from
// the app's own metadata, and otherwise names a real exception. Vela has no
// per-app activity source, so nothing here is invented to fill the space.
function appDetail(app) {
  if (!app.supported) return 'Not supported on this platform';
  if (app.kind === 'connected-web') return app.description || 'Connected web app';
  return app.description || app.category || '';
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

// Home is the app launcher: your apps, and one way to add another. Engine
// counters, version strings, system widgets and a second catalog promotion are
// not part of using an app, so they live in Settings and the Library instead.
export default function Home() {
  const { apps, openApp, openingId } = useApps();

  const loading = apps === null;
  const installed = (apps || []).filter((a) => a.installed);
  const today = new Date().toLocaleDateString(undefined, {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
  });

  return (
    <WorkspacePage>
      <div className="page-inner">
        <header className="home-hero">
          <div>
            <h1 className="home-greeting">{greeting()}</h1>
            <p className="home-meta">{today}</p>
          </div>
        </header>

        <section className="home-section">
          <div className="page-head-row home-section-head">
            <h2 className="section-head">Your apps</h2>
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
              <h2>Add your first app</h2>
              <p>Apps you add run here, on your own computer. Pick one to get started.</p>
              <Link className="btn btn-primary" to="/library">
                Browse Library
              </Link>
            </div>
          ) : (
            <div className="tiles-grid">
              {installed.map((app) => {
                const meta = appDetail(app);
                const busy = openingId === app.id;
                return (
                  <button
                    key={app.id}
                    type="button"
                    className="tile-card"
                    disabled={busy}
                    aria-label={`Open ${app.name}`}
                    onClick={() => openApp(app.id, { returnTo: '/' })}
                  >
                    <AppIcon app={app} size={40} />
                    <span className="tile-card-text">
                      <span className="tile-card-name">{app.name}</span>
                      <span className="tile-card-meta">{busy ? `Opening ${app.name}…` : meta}</span>
                    </span>
                  </button>
                );
              })}
              <Link to="/library" className="tile-add">
                <Plus size={20} aria-hidden="true" />
                <span className="tile-add-name">Add an app</span>
                <span className="tile-add-sub">From the Library</span>
              </Link>
            </div>
          )}
        </section>
      </div>
    </WorkspacePage>
  );
}
