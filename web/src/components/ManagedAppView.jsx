import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowClockwise, ArrowSquareOut, Gear, Stop } from '@phosphor-icons/react';
import { api, managedState } from '../api.js';
import { useApps } from '../store.jsx';
import AppIcon from './AppIcon.jsx';
import Shell from './Shell.jsx';
import WorkspacePage from './WorkspacePage.jsx';
import ManagedAppPanel from './ManagedAppPanel.jsx';
import { openExternal } from '../clipboard.js';

// A managed web app in a Vela window.
//
// The application is served on an address of its own, so this frame is
// cross-origin by construction: Vela cannot read it, style it or sign into it,
// and does not try. What it does own is getting in. A launch ticket is asked
// for, spent once by the frame navigating to it, and never kept -- reloading
// asks for a new one rather than replaying the old.
//
// Closing this window does not stop the service. That distinction is the one
// people get wrong about hosted software, so the window says so rather than
// leaving it to be discovered.
export default function ManagedAppView({ app, onStatus }) {
  const { pushToast } = useApps();
  const [ticket, setTicket] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const cancelled = useRef(false);
  const state = managedState(app);
  const external = app.managed?.embedding === 'external';
  const origin = app.managed?.origin || '';

  useEffect(() => () => void (cancelled.current = true), []);

  const open = useCallback(
    async ({ intoFrame }) => {
      setBusy(true);
      setError('');
      try {
        const link = await api.openManaged(app.id);
        if (cancelled.current) return null;
        if (intoFrame) setTicket(link);
        return link;
      } catch (failure) {
        if (!cancelled.current) setError(failure.message);
        return null;
      } finally {
        if (!cancelled.current) setBusy(false);
      }
    },
    [app.id],
  );

  // A window shows the app; it does not start one behind the person's back. The
  // ticket is asked for when this view is opened, which is the deliberate act,
  // and again on an explicit reload.
  useEffect(() => {
    if (external) return;
    setTicket(null);
    open({ intoFrame: true });
  }, [app.id, app.managed?.generation, attempt, external, open]);

  const openInBrowser = async () => {
    const link = await open({ intoFrame: false });
    if (link) openExternal(link.url);
  };

  const stop = async () => {
    setBusy(true);
    try {
      await api.stopManaged(app.id);
      setTicket(null);
      onStatus?.();
      pushToast?.(`${app.name} stopped.`);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };

  const start = async () => {
    setBusy(true);
    setError('');
    try {
      await api.startManaged(app.id);
      onStatus?.();
      setAttempt((value) => value + 1);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Shell>
      <WorkspacePage
        scroll={false}
        title={app.name}
        subtitle={origin.replace(/^https?:\/\//, '') || 'Managed web app'}
        lead={<AppIcon app={app} size={26} />}
        actions={
          <>
            <span className={`badge badge-managed badge-${state.tone}`}>{state.label}</span>
            <button
              className="btn btn-small btn-icon"
              onClick={() => setAttempt((value) => value + 1)}
              disabled={busy || external}
              aria-label={`Reload ${app.name}`}
            >
              <ArrowClockwise size={16} aria-hidden="true" />
            </button>
            <button
              className="btn btn-small btn-compact"
              onClick={openInBrowser}
              disabled={busy}
              aria-label="Open in browser"
            >
              <ArrowSquareOut size={16} aria-hidden="true" />
              <span className="btn-label">Open in browser</span>
            </button>
            <button
              className="btn btn-small btn-compact"
              onClick={stop}
              disabled={busy || state.tone !== 'good'}
              aria-label={`Stop ${app.name}`}
            >
              <Stop size={16} aria-hidden="true" />
              <span className="btn-label">Stop</span>
            </button>
            <button
              className="btn btn-small btn-compact"
              onClick={() => setSettingsOpen(true)}
              aria-label={`${app.name} settings`}
            >
              <Gear size={16} aria-hidden="true" />
              <span className="btn-label">Settings</span>
            </button>
          </>
        }
      >
        <div className="appview appview-hub managed-app-view">
          <div className="connected-app-help">
            <span>{origin.replace(/^https?:\/\//, '')}</span>
            <span>
              {app.name} keeps its own account and data. Closing this window leaves it running; use
              Stop to end it.
            </span>
          </div>
          {error ? (
            <div className="appview-interstitial" role="alert">
              <h2>{app.name} is not open</h2>
              <p>{error}</p>
              <div className="actions">
                <button className="btn btn-primary" onClick={start} disabled={busy}>
                  Start it
                </button>
                <button className="btn" onClick={() => setSettingsOpen(true)}>
                  Open settings
                </button>
              </div>
            </div>
          ) : external ? (
            <div className="appview-interstitial">
              <h2>{app.name} opens in a browser tab</h2>
              <p>
                This app asks not to be shown inside another page, and Vela does not overrule it.
              </p>
              <div className="actions">
                <button className="btn btn-primary" onClick={openInBrowser} disabled={busy}>
                  Open in browser
                </button>
              </div>
            </div>
          ) : ticket ? (
            <iframe
              key={ticket.url}
              className="appview-frame"
              src={ticket.url}
              title={`${app.name} workspace`}
              referrerPolicy="no-referrer"
              allow="clipboard-write; fullscreen"
            />
          ) : (
            <div className="appview-interstitial" aria-live="polite">
              <h2>Opening {app.name}</h2>
              <p>{state.detail || 'Starting the app and preparing its address.'}</p>
            </div>
          )}
          {settingsOpen && (
            <ManagedAppPanel
              app={app}
              onClose={() => setSettingsOpen(false)}
              onChanged={onStatus}
            />
          )}
        </div>
      </WorkspacePage>
    </Shell>
  );
}
