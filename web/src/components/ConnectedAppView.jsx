import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { ArrowSquareOut, ArrowClockwise, CaretLeft } from '@phosphor-icons/react';
import { connectedAddress, connectedDocument } from '../connected-web.js';
import AppIcon from './AppIcon.jsx';
import ConnectedAppForm from './ConnectedAppForm.jsx';

export default function ConnectedAppView({ app }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [attempt, setAttempt] = useState(0);
  const [editing, setEditing] = useState(false);
  let document, address, error;
  try {
    address = connectedAddress(app.url, window.location.origin);
    document = connectedDocument(app.url, app.name, window.location.origin);
  } catch (failure) {
    error = failure.message;
  }
  return (
    <div className="appview appview-compact connected-app-view">
      <header className="appview-chrome">
        <button
          className="btn btn-ghost appview-back"
          aria-label="Back to Apps"
          onClick={() => navigate(location.state?.returnTo || '/apps')}
        >
          <CaretLeft size={16} /> Apps
        </button>
        <div className="appview-title">
          <AppIcon app={app} size={30} />
          <span className="appview-name">{app.name}</span>
        </div>
        <button className="btn btn-small" onClick={() => setEditing(true)}>
          Edit connection
        </button>
        <button
          className="btn btn-small"
          onClick={() => setAttempt((value) => value + 1)}
          disabled={Boolean(error)}
          aria-label="Reload web app"
        >
          <ArrowClockwise size={16} />
        </button>
        {address && (
          <a
            className="btn btn-small"
            href={address.href}
            target="_blank"
            rel="noopener noreferrer"
          >
            <ArrowSquareOut size={16} /> Open in browser
          </a>
        )}
      </header>
      <div className="connected-app-help">
        <span>{address?.host || 'Web app'}</span>
        <span>
          Blank page or sign-in trouble? Open in browser. Save your work before leaving or
          reloading.
        </span>
      </div>
      {error ? (
        <div className="appview-interstitial" role="alert">
          <h2>Update this connection</h2>
          <p>{error}</p>
        </div>
      ) : (
        <iframe
          key={`${app.url}:${attempt}`}
          className="appview-frame"
          srcDoc={document}
          title={`${app.name} workspace`}
          referrerPolicy="no-referrer"
          sandbox="allow-scripts allow-same-origin allow-forms allow-downloads allow-popups"
        />
      )}
      {editing && <ConnectedAppForm app={app} onClose={() => setEditing(false)} />}
    </div>
  );
}
