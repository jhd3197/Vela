// An installed app, in a window on a desktop.
//
// The frame around it is `WindowFrame`; what is inside it is the same sandboxed
// iframe and the same bridge the full-screen app page uses, through the same
// `useAppFrame`. The only things that differ are the chrome and the fact that
// this one can be minimized without ending anything.
import { useCallback, useRef, useState } from 'react';
import AppIcon from '../components/AppIcon.jsx';
import { reportAppActivity } from '../components/SecurityProvider.jsx';
import { useApps, useAppStatus } from '../store.jsx';
import useAppFrame from './view-lifecycle.js';

export default function AppWindow({ view, frameRef, onDirty, onDisconnect }) {
  const { apps } = useApps();
  const { status } = useAppStatus(view.appId);
  const summary = apps?.find((item) => item.id === view.appId);
  const app = summary && { ...summary, ...status };
  const isolated = summary?.schemaVersion === 2;
  const surface = summary?.view?.surface || 'embedded';
  const running = Boolean(app?.running && app?.url && surface === 'embedded' && view.available);
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

  if (!app) {
    return (
      <div className="window-state" role="status">
        <p>Looking for this app…</p>
      </div>
    );
  }

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
        className="window-frame-app"
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
      {(!ready || !loaded) && !error ? (
        <div className="window-state window-state-over" role="status">
          <AppIcon app={app} size={40} />
          <p>Starting {app.name}…</p>
        </div>
      ) : null}
      {error ? (
        <div className="window-state window-state-over" role="alert">
          <p>{error}</p>
        </div>
      ) : null}
    </>
  );
}
