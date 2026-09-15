import { CaretRight } from '@phosphor-icons/react';
import AppIcon from './AppIcon.jsx';
import StatusBadge from './StatusBadge.jsx';

// Row used by Apps and Library, inside a .group-list card. Clicking the row
// opens the detail drawer; the Library variant keeps an inline Install
// button for apps that aren't installed yet.
export default function AppRow({ app, busy, onAction, onDetails, variant = 'apps' }) {
  const meta =
    app.kind === 'connected-web'
      ? 'Connected web app'
      : app.running
        ? 'Running locally'
        : [`v${app.version}`, app.category].filter(Boolean).join(' · ');

  return (
    <button
      className={`group-row${!app.supported ? ' group-row-unsupported' : ''}`}
      onClick={() => onDetails(app)}
    >
      <AppIcon app={app} size={38} />
      <span className="group-row-main">
        <span className="group-row-name">{app.name}</span>
        <span className={`group-row-meta${app.running ? ' group-row-meta-accent' : ''}`}>
          {app.supported ? meta : 'Not supported on this platform'}
        </span>
      </span>
      <span className="group-row-side">
        <StatusBadge app={app} />
        {variant === 'library' && app.supported && !app.installed && (
          <span
            className={`btn btn-primary btn-small${busy ? ' disabled' : ''}`}
            role="button"
            aria-disabled={busy}
            onClick={(e) => {
              e.stopPropagation();
              if (!busy) onAction(app.id, 'install');
            }}
          >
            Install
          </span>
        )}
        <CaretRight size={15} className="caret" />
      </span>
    </button>
  );
}
