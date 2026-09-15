import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams, useLocation, useBlocker } from 'react-router-dom';
import { api, isProcessApp } from '../api.js';
import { useApps, useAppStatus } from '../store.jsx';
import AppIcon from '../components/AppIcon.jsx';
import Shell from '../components/Shell.jsx';
import WorkspacePage from '../components/WorkspacePage.jsx';
import AppDataMigration from '../components/AppDataMigration.jsx';
import AppConnection from '../components/AppConnection.jsx';
import AppSettingsDrawer from '../components/AppSettingsDrawer.jsx';
import { PermissionNotice } from '../components/AppPermissions.jsx';
import Dialog from '../components/ui/Dialog.jsx';
import { createBridge } from '../bridge/host.js';
import useViewport from '../hooks/useViewport.js';
import { intersectRect, occlusionOf, visibleRect } from '../viewport.js';
import ConnectedAppView from '../components/ConnectedAppView.jsx';
import { reportAppActivity } from '../components/SecurityProvider.jsx';

export default function AppView() {
  const { id } = useParams();
  const [attempt, setAttempt] = useState(0);
  const { apps } = useApps();
  const app = apps?.find((item) => item.id === id);
  if (app?.kind === 'connected-web') return <ConnectedAppView key={id} app={app} />;
  return (
    <Workspace key={`${id}:${attempt}`} id={id} retry={() => setAttempt((value) => value + 1)} />
  );
}

function Workspace({ id, retry }) {
  const navigate = useNavigate();
  const location = useLocation();
  const { apps, busyIds, openApp, openingId, runAction } = useApps();
  const { status } = useAppStatus(id);
  const summary = apps?.find((item) => item.id === id);
  const app = summary && { ...summary, ...status };
  const isolated = summary?.schemaVersion === 2;
  const surface = summary?.view?.surface || 'embedded';
  const running = Boolean(app?.running && app?.url && surface === 'embedded');
  const [compact, setCompact] = useState(
    () => localStorage.getItem(`vela.chrome.${id}`) === 'compact',
  );
  const requestedMode = summary?.view?.chrome || 'compact';
  // A removed or unknown app has no declared presentation. Recover it inside the
  // shell so the rail offers a clear path back instead of a bare interstitial.
  const missing = apps !== null && !summary;
  const mode = missing
    ? 'hub'
    : requestedMode === 'seamless' && compact
      ? 'compact'
      : requestedMode;
  const [session, setSession] = useState(null);
  const [error, setError] = useState('');
  const [ready, setReady] = useState(false);
  const [menu, setMenu] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [dirty, setDirty] = useState({ dirty: false, canSave: false });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [confirmLeave, setConfirmLeave] = useState(false);
  // One host-owned measurement, shared with the shell and every other consumer.
  const view = useViewport();
  const frame = useRef(null),
    exitControl = useRef(null),
    bridge = useRef(null),
    cancelButton = useRef(null);
  const menuRef = useRef(null),
    menuButton = useRef(null);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirtyRef.current.dirty && currentLocation.pathname !== nextLocation.pathname,
  );
  const returnTo = useRef(location.state?.returnTo || '/apps');
  const leave = () => {
    dirtyRef.current = { dirty: false, canSave: false };
    if (blocker.state === 'blocked') blocker.proceed();
    else navigate(returnTo.current, { state: { restoreLauncher: true } });
  };
  const requestLeave = () => {
    setMenu(false);
    if (dirtyRef.current.dirty) setConfirmLeave(true);
    else leave();
  };
  const leaveRef = useRef(requestLeave);
  leaveRef.current = requestLeave;

  // The frame's own box, and how much of it the browser is not showing. Host
  // offsets are in the host's coordinate space, so they are converted here
  // rather than handed to the app to reinterpret.
  function viewportContext() {
    const box = mode === 'seamless' ? exitControl.current?.getBoundingClientRect() : null;
    const frameBox = frame.current?.getBoundingClientRect();
    const state = view;
    const visible = visibleRect(state);
    const usable = frameBox ? intersectRect(frameBox, visible) : null;
    return {
      installationId: session?.installationId,
      protocol: 1,
      capabilities: session?.capabilities || [],
      unavailableCapabilities: session?.unavailableCapabilities || [],
      theme: document.documentElement.dataset.theme || 'dark',
      locale: navigator.language,
      view: { surface, chrome: mode },
      viewport: {
        width: frame.current?.clientWidth || innerWidth,
        height: frame.current?.clientHeight || innerHeight,
        visualHeight: usable ? usable.height : state.height || innerHeight,
        insets: frameBox
          ? occlusionOf(frameBox, visible)
          : { top: 0, right: 0, bottom: 0, left: 0 },
        hostControl: box
          ? {
              x: box.x - (frameBox?.x || 0),
              y: box.y - (frameBox?.y || 0),
              width: box.width,
              height: box.height,
            }
          : null,
      },
    };
  }
  const contextRef = useRef(viewportContext);
  contextRef.current = viewportContext;

  useEffect(() => {
    if (!running || !isolated) {
      setSession(null);
      setReady(false);
      setError('');
      return;
    }
    let disposed = false;
    api
      .openSession(id)
      .then((value) => {
        if (disposed)
          fetch('/api/app/session', {
            method: 'DELETE',
            headers: { Authorization: `Bearer ${value.token}` },
          }).catch(() => {});
        else setSession(value);
      })
      .catch((failure) => {
        if (!disposed) setError(failure.message);
      });
    return () => {
      disposed = true;
    };
  }, [id, running, isolated]);

  useEffect(() => {
    if (!session || !frame.current || !running) return;
    const active = createBridge({
      frame: frame.current,
      session,
      context: contextRef.current(),
      onDirty: (state) => {
        // Editing inside the frame is real activity. The host cannot see the
        // keystrokes, so the app's own dirty report stands in for them and
        // keeps the inactivity lock from firing mid-sentence.
        reportAppActivity();
        setDirty(state);
      },
      onNavigate: () => leaveRef.current(),
      onReady: () => setReady(true),
      onError: setError,
    });
    bridge.current = active;
    const update = () => active.updateContext(contextRef.current());
    const resize = new ResizeObserver(update);
    resize.observe(frame.current);
    const theme = new MutationObserver(update);
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => {
      active.close();
      bridge.current = null;
      resize.disconnect();
      theme.disconnect();
    };
  }, [session, running]);

  // The shared service already coalesces viewport events; the app only needs
  // the resulting geometry once it settles.
  useEffect(() => {
    bridge.current?.updateContext(contextRef.current());
  }, [mode, view]);
  useEffect(() => {
    const warn = (event) => {
      if (dirtyRef.current.dirty) {
        event.preventDefault();
        event.returnValue = '';
      }
    };
    addEventListener('beforeunload', warn);
    return () => removeEventListener('beforeunload', warn);
  }, []);
  useEffect(() => {
    if (!menu) return;
    menuRef.current?.querySelector('button')?.focus();
    const dismiss = (event) => {
      if (event.type === 'keydown' && event.key === 'Escape') {
        setMenu(false);
        menuButton.current?.focus();
      }
      if (event.type === 'pointerdown' && !exitControl.current?.contains(event.target))
        setMenu(false);
    };
    addEventListener('keydown', dismiss);
    addEventListener('pointerdown', dismiss);
    return () => {
      removeEventListener('keydown', dismiss);
      removeEventListener('pointerdown', dismiss);
    };
  }, [menu]);

  const toggleCompact = () => {
    localStorage.setItem(`vela.chrome.${id}`, compact ? 'seamless' : 'compact');
    setCompact(!compact);
    setMenu(false);
  };
  const saveAndLeave = async () => {
    setSaving(true);
    setSaveError('');
    try {
      await bridge.current.save();
      leave();
    } catch (failure) {
      setSaveError(failure.message);
    } finally {
      setSaving(false);
    }
  };
  // Everything but `seamless` is hosted by the shell: the rail names and selects
  // the app and the contextual header carries its controls, so no second app
  // bar is drawn above the frame.
  const hosted = mode !== 'seamless';
  const content = (
    <div className={`appview appview-${mode}${hosted ? ' appview-hosted' : ''}`}>
      {mode === 'seamless' && (
        <div className="appview-exit" ref={exitControl}>
          <button
            ref={menuButton}
            className="btn appview-exit-button"
            aria-label="Vela app menu"
            aria-expanded={menu}
            aria-controls="vela-app-menu"
            onClick={() => setMenu(!menu)}
          >
            Vela <span aria-hidden="true">⌄</span>
          </button>
          {menu && (
            <div id="vela-app-menu" className="appview-menu" ref={menuRef}>
              <strong>{app?.name || 'App'}</strong>
              <button onClick={requestLeave}>Return to apps</button>
              <button onClick={requestLeave}>Close app view</button>
              <button onClick={toggleCompact}>Show compact bar</button>
              {app?.installed && (
                <button
                  onClick={() => {
                    setMenu(false);
                    setSettingsOpen(true);
                  }}
                >
                  App settings
                </button>
              )}
            </div>
          )}
        </div>
      )}
      {apps === null && (
        <div className="appview-interstitial">
          <p>Loading app…</p>
        </div>
      )}
      {app?.installed && <AppDataMigration app={app} />}
      {app?.installed && <AppConnection app={app} setupOnly />}
      {app?.installed && <PermissionNotice app={app} onReview={() => setSettingsOpen(true)} />}
      {apps !== null && !app && (
        <div className="appview-interstitial">
          <h2>App not found</h2>
          <button className="btn" onClick={requestLeave}>
            Back to Apps
          </button>
        </div>
      )}
      {app && !app.installed && (
        <div className="appview-interstitial">
          <AppIcon app={app} size={64} />
          <h2>{app.name}</h2>
          <p>Install this app to open its workspace.</p>
          <button
            className="btn btn-primary"
            disabled={busyIds.has(id)}
            onClick={() => runAction(id, 'install')}
          >
            Install {app.name}
          </button>
        </div>
      )}
      {app?.installed && !app.running && isProcessApp(app) && (
        <div className="appview-interstitial">
          <h2>{app.name} is not running</h2>
          <p>Opening it starts {app.name} on this computer.</p>
          <button
            className="btn btn-primary"
            disabled={busyIds.has(id) || openingId === id}
            onClick={() => openApp(id, { returnTo: returnTo.current })}
          >
            {openingId === id ? `Opening ${app.name}…` : `Open ${app.name}`}
          </button>
        </div>
      )}
      {app?.installed && surface === 'external' && (
        <div className="appview-interstitial">
          <h2>{app.name}</h2>
          <p>This app opens its own website.</p>
          <a className="btn" href={summary.view.url} target="_blank" rel="noopener noreferrer">
            Open {app.name}
          </a>
        </div>
      )}
      {app?.installed && surface === 'none' && (
        <div className="appview-interstitial">
          <h2>{app.name}</h2>
          <p>This app has no visual workspace.</p>
        </div>
      )}
      {running && isolated && !ready && !error && (
        <div className="appview-loading" role="status">
          Connecting to {app.name}…
        </div>
      )}
      {error && (
        <div className="appview-failure" role="alert">
          <h2>Couldn’t open the app</h2>
          <p>{error}</p>
          <button className="btn" onClick={retry}>
            Retry
          </button>
          <button className="btn" onClick={requestLeave}>
            Return to apps
          </button>
        </div>
      )}
      {running && (!isolated || session) && (
        <iframe
          key={session?.token || app.url}
          ref={frame}
          className="appview-frame"
          src={app.url}
          title={app.name}
          sandbox={isolated ? 'allow-scripts' : undefined}
          referrerPolicy="no-referrer"
          onLoad={(event) => {
            if (isolated && event.currentTarget.dataset.loaded) {
              bridge.current?.close();
              setError('The app navigated away from its workspace. Reopen it to reconnect.');
            }
            event.currentTarget.dataset.loaded = 'true';
          }}
          onError={() =>
            setError('The app could not load. Your Vela controls are still available.')
          }
        />
      )}
      <Dialog
        open={confirmLeave || blocker.state === 'blocked'}
        pending={saving}
        initialFocusRef={cancelButton}
        returnFocusRef={menuButton}
        className="appview-dialog"
        aria-labelledby="unsaved-title"
        onClose={() => {
          setConfirmLeave(false);
          if (blocker.state === 'blocked') blocker.reset();
        }}
      >
        <h2 id="unsaved-title">Save changes before leaving?</h2>
        <p>{app?.name || 'This app'} has unsaved work.</p>
        {saveError && <p role="alert">{saveError}</p>}
        <div className="appview-dialog-actions">
          {dirty.canSave && (
            <button className="btn btn-primary" disabled={saving} onClick={saveAndLeave}>
              {saving ? 'Saving…' : 'Save and leave'}
            </button>
          )}
          <button className="btn" disabled={saving} onClick={leave}>
            Discard and leave
          </button>
          <button
            ref={cancelButton}
            className="btn"
            disabled={saving}
            onClick={() => {
              setConfirmLeave(false);
              if (blocker.state === 'blocked') blocker.reset();
            }}
          >
            Cancel
          </button>
        </div>
      </Dialog>
      {settingsOpen && app?.installed && (
        <AppSettingsDrawer app={app} onClose={() => setSettingsOpen(false)} />
      )}
    </div>
  );
  if (!hosted) return content;
  // The rail already names and selects the open app, so the hosted workspace
  // uses the contextual header instead of a second app bar. The rail stays on
  // screen at phone widths too, so switching apps is one tap away while the
  // app's own panes change beneath. A seamless app that was asked to show the
  // bar can hide it again from here.
  return (
    <Shell>
      <WorkspacePage
        scroll={false}
        className="app-workspace"
        title={app?.name || (missing ? 'App unavailable' : 'App')}
        subtitle={app?.description || undefined}
        lead={app ? <AppIcon app={app} size={26} /> : null}
        actions={
          <>
            {requestedMode === 'seamless' && !missing && (
              <button className="btn btn-small" onClick={toggleCompact}>
                Hide app bar
              </button>
            )}
            {app?.installed && (
              <button className="btn btn-small" onClick={() => setSettingsOpen(true)}>
                App settings
              </button>
            )}
          </>
        }
      >
        {content}
      </WorkspacePage>
    </Shell>
  );
}
