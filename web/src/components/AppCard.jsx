import { useNavigate } from 'react-router-dom';
import StatusBadge from './StatusBadge.jsx';
import AppIcon from './AppIcon.jsx';

// Home page rich card: colored tile, name, one-line description, Running
// badge (or Install for available ones), chevron. Click opens the embedded
// app view; available apps install in place first via the button.
export default function AppCard({ app, busy, onAction }) {
  const navigate = useNavigate();

  const open = () => {
    if (app.installed) navigate(`/app/${app.id}`);
  };

  return (
    <article
      className={`home-card${app.installed ? ' home-card-clickable' : ''}`}
      onClick={open}
      role={app.installed ? 'button' : undefined}
      tabIndex={app.installed ? 0 : undefined}
      onKeyDown={(e) => {
        if (app.installed && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault();
          open();
        }
      }}
      aria-label={app.installed ? `Open ${app.name}` : app.name}
    >
      <div className="home-card-top">
        <AppIcon app={app} size={48} />
        {app.running ? (
          <StatusBadge app={app} />
        ) : !app.installed && app.supported ? (
          <button
            className="btn btn-small"
            disabled={busy}
            onClick={(e) => {
              e.stopPropagation();
              onAction(app.id, 'install');
            }}
          >
            Install
          </button>
        ) : null}
      </div>
      <h3 className="home-card-name">{app.name}</h3>
      <p className="home-card-desc">{app.description}</p>
      <div className="home-card-foot">
        <span className="home-card-meta">
          v{app.version}
          {app.category ? ` · ${app.category}` : ''}
        </span>
        {app.installed && (
          <svg
            className="home-card-chevron"
            viewBox="0 0 24 24"
            width="16"
            height="16"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="m9 6 6 6-6 6" />
          </svg>
        )}
      </div>
    </article>
  );
}
