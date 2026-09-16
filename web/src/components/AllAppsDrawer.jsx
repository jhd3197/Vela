import { useMemo, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { MagnifyingGlass, Plus, X } from '@phosphor-icons/react';
import { useApps } from '../store.jsx';
import AppIcon from './AppIcon.jsx';
import Drawer from './ui/Drawer.jsx';

// Every installed app in one place, over whatever page is open. The rail holds
// as many shortcuts as fit and scrolls the rest; this is where the whole set
// lives, with what is running called out first because that is what people are
// usually reaching for.
export default function AllAppsDrawer({ summaries, onClose }) {
  const { apps, openApp } = useApps();
  const location = useLocation();
  const [query, setQuery] = useState('');

  const installed = useMemo(
    () =>
      (apps || [])
        .filter((app) => app.installed)
        .slice()
        .sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id)),
    [apps],
  );
  const needle = query.trim().toLowerCase();
  const matching = needle
    ? installed.filter((app) => app.name.toLowerCase().includes(needle))
    : installed;
  const running = matching.filter((app) => app.running);

  const open = (app) => {
    onClose();
    openApp(app.id, { returnTo: location.pathname });
  };

  // The one line under a running app: what its own widget said about it, when
  // it said anything. Nothing is invented for an app that publishes nothing.
  const caption = (app) =>
    (summaries || []).find((summary) => summary.appId === app.id && summary.summary?.caption)
      ?.summary.caption || 'Running';

  return (
    <Drawer open onClose={onClose} aria-label="All apps" panelClassName="all-apps">
      <div className="drawer-header">
        <div className="drawer-title-row">
          <h2 className="drawer-name">All apps</h2>
        </div>
        <button className="drawer-close" onClick={onClose} aria-label="Close all apps">
          <X size={20} />
        </button>
      </div>
      <div className="all-apps-search">
        <MagnifyingGlass size={15} aria-hidden="true" />
        <input
          type="search"
          aria-label="Find an app"
          placeholder="Find an app…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      <div className="drawer-body">
        {running.length > 0 && (
          <section className="all-apps-section">
            <h3 className="section-head">Running now</h3>
            <ul className="all-apps-running">
              {running.map((app) => (
                <li key={app.id}>
                  <button type="button" onClick={() => open(app)}>
                    <AppIcon app={app} size={32} />
                    <span className="all-apps-running-text">
                      <span className="all-apps-name">{app.name}</span>
                      <span className="all-apps-meta">{caption(app)}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
        <section className="all-apps-section">
          <h3 className="section-head">Installed</h3>
          {matching.length === 0 ? (
            <p className="desk-empty" role="status">
              {installed.length === 0
                ? 'No apps installed yet.'
                : `No apps match “${query.trim()}”.`}
            </p>
          ) : (
            <div className="all-apps-grid">
              {matching.map((app) => (
                <button
                  key={app.id}
                  type="button"
                  className="all-apps-tile"
                  aria-label={`Open ${app.name}`}
                  onClick={() => open(app)}
                >
                  <AppIcon app={app} size={48} />
                  <span className="all-apps-name">{app.name}</span>
                  {app.version ? <span className="all-apps-meta">{app.version}</span> : null}
                </button>
              ))}
              <Link className="all-apps-tile all-apps-add" to="/library" onClick={onClose}>
                <span className="all-apps-add-mark" aria-hidden="true">
                  <Plus size={20} />
                </span>
                <span className="all-apps-name">Add an app</span>
                <span className="all-apps-meta">From the Library</span>
              </Link>
            </div>
          )}
        </section>
      </div>
    </Drawer>
  );
}
