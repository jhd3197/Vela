import { useState } from 'react';
import { ArrowSquareOut, ArrowClockwise, PencilSimple } from '@phosphor-icons/react';
import { connectedAddress, connectedDocument } from '../connected-web.js';
import AppIcon from './AppIcon.jsx';
import ConnectedAppForm from './ConnectedAppForm.jsx';
import Shell from './Shell.jsx';
import WorkspacePage from './WorkspacePage.jsx';

// A connected service keeps its own origin, login and data. Vela only frames
// it: the rail and header around the frame are the host's, the contents are
// not, and the browser fallback stays available because an iframe load cannot
// prove the service rendered.
export default function ConnectedAppView({ app }) {
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
    <Shell>
      <WorkspacePage
        scroll={false}
        title={app.name}
        subtitle={address?.host || 'Connected web app'}
        lead={<AppIcon app={app} size={26} />}
        actions={
          <>
            <button
              className="btn btn-small btn-compact"
              aria-label="Edit connection"
              onClick={() => setEditing(true)}
            >
              <PencilSimple size={16} aria-hidden="true" />
              <span className="btn-label">Edit connection</span>
            </button>
            <button
              className="btn btn-small btn-icon"
              onClick={() => setAttempt((value) => value + 1)}
              disabled={Boolean(error)}
              aria-label="Reload web app"
            >
              <ArrowClockwise size={16} aria-hidden="true" />
            </button>
            {address && (
              <a
                className="btn btn-small btn-compact"
                aria-label="Open in browser"
                href={address.href}
                target="_blank"
                rel="noopener noreferrer"
              >
                <ArrowSquareOut size={16} aria-hidden="true" />
                <span className="btn-label">Open in browser</span>
              </a>
            )}
          </>
        }
      >
        <div className="appview appview-hub connected-app-view">
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
      </WorkspacePage>
    </Shell>
  );
}
