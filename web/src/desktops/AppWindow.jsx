// An installed app, in a window on a desktop.
//
// The frame around it is `WindowFrame`; what is inside it is the same sandboxed
// iframe and the same bridge the full-screen app page uses, through the same
// `useAppFrame`. The only things that differ are the chrome and the fact that
// this one can be minimized without ending anything.
import { useCallback, useEffect, useRef, useState } from 'react';
import AppIcon from '../components/AppIcon.jsx';
import { reportAppActivity } from '../components/SecurityProvider.jsx';
import { useApps, useAppStatus } from '../store.jsx';
import useAppFrame from './view-lifecycle.js';

/** How long the starting state waits before saying so, as the full page does. */
const STARTING_TIMEOUT_MS = 10000;

/** The handoff: the icon's beat to leave while the real content rises. */
const HANDOFF_MS = 240;

/** How long a wait may be before the loading state renders at all. */
const OVERLAY_GRACE_MS = 200;

export default function AppWindow({ view, frameRef, onDirty, onDisconnect, onBusy }) {
  const { apps } = useApps();
  const summary = apps?.find((item) => item.id === view.appId);
  // Reload starts the session, the bridge and the frame over from nothing —
  // the same thing the full page's retry does by remounting its workspace.
  const [attempt, setAttempt] = useState(0);

  if (!view.available) {
    return (
      <div className="window-state" role="status">
        <p>
          {view.unavailableReason === 'reinstalled'
            ? 'This app was reinstalled. Open it again to use this window.'
            : 'This app is no longer installed.'}
        </p>
      </div>
    );
  }

  if (!summary) {
    return (
      <div className="window-state" role="status">
        <p>Looking for this app…</p>
      </div>
    );
  }

  return (
    <AppWindowContent
      key={attempt}
      view={view}
      summary={summary}
      frameRef={frameRef}
      onDirty={onDirty}
      onDisconnect={onDisconnect}
      onBusy={onBusy}
      onReload={() => setAttempt((value) => value + 1)}
    />
  );
}

function AppWindowContent({ view, summary, frameRef, onDirty, onDisconnect, onBusy, onReload }) {
  const { status } = useAppStatus(view.appId);
  const app = { ...summary, ...status };
  const isolated = summary.schemaVersion === 2;
  const surface = summary.view?.surface || 'embedded';
  const running = Boolean(app.running && app.url && surface === 'embedded' && view.available);
  const [loaded, setLoaded] = useState(false);

  // The context a window sends is smaller than the page's: a window has no
  // seamless mode and no exit control inside the frame, so there is no host
  // control to describe. The rest is the same contract.
  const contextRef = useRef(() => ({}));
  const { session, ready, error, setError, disconnect } = useAppFrame({
    appId: view.appId,
    enabled: running && isolated,
    frameRef,
    contextRef,
    onDirty: (state) => {
      // The host cannot see keystrokes inside the frame, so the app's own
      // report of unsaved work stands in for them and keeps the inactivity
      // lock from firing mid-sentence.
      reportAppActivity();
      onDirty?.(state);
    },
  });

  contextRef.current = () => ({
    installationId: session?.installationId,
    protocol: 1,
    capabilities: session?.capabilities || [],
    unavailableCapabilities: session?.unavailableCapabilities || [],
    theme: document.documentElement.dataset.theme || 'dark',
    locale: navigator.language,
    view: { surface, chrome: 'window' },
    viewport: {
      width: frameRef.current?.clientWidth || 0,
      height: frameRef.current?.clientHeight || 0,
      visualHeight: frameRef.current?.clientHeight || 0,
      insets: { top: 0, right: 0, bottom: 0, left: 0 },
      hostControl: null,
    },
  });

  const onLoad = useCallback(
    (event) => {
      if (isolated && event.currentTarget.dataset.loaded) {
        disconnect();
        onDisconnect?.();
        setError('The app navigated away from its workspace. Reopen it to reconnect.');
        return;
      }
      event.currentTarget.dataset.loaded = 'true';
      setLoaded(true);
    },
    [isolated, disconnect, onDisconnect, setError],
  );

  // What "still starting" means depends on the app. An isolated app answers
  // `vela:ready` over its bridge; a legacy frame has no bridge to answer with,
  // so its own load event is all there is to wait for. Waiting for a ready
  // that can never come is how the starting state used to stay up forever.
  const starting = running && !error && (isolated ? !ready || !loaded : !loaded);

  // If the app never gets there, say so and offer a way out rather than
  // spinning forever — the same bargain the full page makes.
  const [timedOut, setTimedOut] = useState(false);
  useEffect(() => {
    if (!starting) {
      setTimedOut(false);
      return undefined;
    }
    const timer = setTimeout(() => setTimedOut(true), STARTING_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [starting]);

  // The bar owns the wait: the hairline and the "Opening…" label live there,
  // reported up so the frame can draw them. A stall stops the sweep.
  useEffect(() => {
    onBusy?.(starting && !timedOut);
    return () => onBusy?.(false);
  }, [starting, timedOut, onBusy]);

  // When the app arrives, the icon gets one beat to leave while the content
  // rises — then the overlay is gone rather than snapped away mid-frame.
  const [departed, setDeparted] = useState(false);
  useEffect(() => {
    if (starting) {
      setDeparted(false);
      return undefined;
    }
    const timer = setTimeout(() => setDeparted(true), HANDOFF_MS);
    return () => clearTimeout(timer);
  }, [starting]);

  // The skip, honestly: the frame underneath is hidden until the app answers,
  // and the loading state only renders at all once the wait has lasted a
  // moment. An app that answers inside the grace period opens straight into
  // content — showing it early and covering it a beat later is the flash this
  // replaces.
  const [showOverlay, setShowOverlay] = useState(false);
  useEffect(() => {
    if (!starting) return undefined;
    const timer = setTimeout(() => setShowOverlay(true), OVERLAY_GRACE_MS);
    return () => clearTimeout(timer);
  }, [starting]);
  const overlay = showOverlay && (starting || !departed);

  if (!running) {
    return (
      <div className="window-state" role="status">
        <p>
          {surface === 'embedded'
            ? `${app.name} is not running.`
            : `${app.name} opens outside this window.`}
        </p>
      </div>
    );
  }

  return (
    <>
      <iframe
        key={session?.token || app.url}
        ref={frameRef}
        className={`window-frame-app${starting ? '' : ' is-revealed'}`}
        src={app.url}
        title={app.name}
        // The same sandbox the full-screen page uses. Making the frame
        // same-origin would make the host's job easier and the boundary
        // meaningless.
        sandbox={isolated ? 'allow-scripts' : undefined}
        referrerPolicy="no-referrer"
        onLoad={onLoad}
        onError={() => setError('The app could not load. Your Vela controls are still available.')}
      />
      {overlay ? (
        <div
          className={`window-state window-state-over window-starting${starting ? '' : ' is-leaving'}`}
          role="status"
        >
          {/* Nothing but the app's own icon owns the wait — no skeleton rows
              to mistake for content. Text appears only when there is something
              to say: the wait ran long, and here is the way out. */}
          <span className={`window-starting-icon${timedOut || !starting ? '' : ' is-waiting'}`}>
            <AppIcon app={app} size={64} />
          </span>
          {timedOut ? (
            <>
              <p className="window-starting-title">This app did not respond</p>
              <p className="window-starting-sub">{app.name} has not finished loading.</p>
              <button type="button" className="btn btn-primary" onClick={onReload}>
                Reload
              </button>
            </>
          ) : null}
        </div>
      ) : null}
      {error ? (
        <div className="window-state window-state-over" role="alert">
          <p className="window-starting-title">Couldn’t open the app</p>
          <p className="window-starting-sub">{error}</p>
          <button type="button" className="btn" onClick={onReload}>
            Retry
          </button>
        </div>
      ) : null}
    </>
  );
}
