// Your apps, as a tile grid.
//
// This is the launcher that used to be the whole of Home: the installed apps,
// each an Open control carrying the app's own short purpose, and one way to
// add another. Nothing here is invented — an app with no description says
// nothing rather than filling the line with activity Vela does not have.
import { Link } from 'react-router-dom';
import { SquaresFour } from '@phosphor-icons/react';
import AppIcon from '../../components/AppIcon.jsx';
import { useApps } from '../../store.jsx';

// The one secondary line under an app name, from the app's own metadata, or a
// real exception when the app cannot run here.
export function appDetail(app) {
  if (!app.supported) return 'Not supported on this platform';
  if (app.kind === 'connected-web') return app.description || 'Connected web app';
  return app.description || app.category || '';
}

export function TileSkeleton() {
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

export default function AppsWidget({ cfg = {}, ctx }) {
  const { apps, openApp, openingId } = useApps();
  const loading = apps === null;
  const installed = (apps || []).filter((app) => app.installed);
  // A widget's own setting wins; otherwise the desk's "Show app names" does.
  const labels = cfg.labels ?? ctx?.desk?.labels !== false;
  const phone = Boolean(ctx?.phone);

  if (loading) {
    return (
      <div className="tiles-grid desk-tiles">
        <p className="sr-only" role="status">
          Loading apps…
        </p>
        <TileSkeleton />
        <TileSkeleton />
        <TileSkeleton />
      </div>
    );
  }

  if (installed.length === 0) {
    return (
      <div className="desk-apps-empty">
        <p>Apps you add run here, on your own computer.</p>
        <Link className="btn btn-primary btn-small" to="/library">
          Browse Library
        </Link>
      </div>
    );
  }

  // Both boards show the apps as a labelled icon grid now — no cramped rows.
  // "Open all" leads to the Launchpad, where the whole set and its actions live.
  return (
    <div className={`tiles-grid desk-tiles desk-tiles-icons${labels ? '' : ' desk-tiles-bare'}`}>
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
            <AppIcon app={app} size={phone ? 52 : 56} />
            <span className="tile-card-text">
              <span className="tile-card-name">{app.name}</span>
              <span className="tile-card-meta">{busy ? `Opening ${app.name}…` : meta}</span>
            </span>
          </button>
        );
      })}
      <Link to="/apps" className="tile-add">
        <SquaresFour size={20} aria-hidden="true" />
        <span className="tile-add-name">Open all</span>
        <span className="tile-add-sub">In the Launchpad</span>
      </Link>
    </div>
  );
}
