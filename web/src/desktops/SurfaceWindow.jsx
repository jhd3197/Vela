// A surface window: a screen described as data, fetched by the hub through
// the owning app's http connection and drawn with Vela's own components.
//
// Two ways to look at one surface. Desktop mode polls the document and
// renders it with `SurfaceView`; screen mode polls a picture of it. Auto
// picks from what the producer's capabilities say. Nothing app-supplied
// executes either way — the document is data and the picture is a picture.
import { useEffect, useRef, useState } from 'react';
import SegControl from '../components/ds/SegControl.jsx';
import SurfaceView from '../components/SurfaceView.jsx';
import { WidgetBoundary } from '../desk/widgets/primitives.jsx';
import { useDesktops } from './DesktopsProvider.jsx';
import { desktopsApi } from './desktopsApi.js';

/** A picture a second, and never more than one request in the air. */
const FRAME_MS = 1000;

/** The document poll when the surface does not name its own floor. */
const DEFAULT_POLL_MS = 4000;

const MODES = [
  { value: 'auto', label: 'Auto' },
  { value: 'screen', label: 'Screen' },
  { value: 'desktop', label: 'Desktop' },
];

/** What the capabilities answer says about taking pictures. Both shapes the
 *  producers use count: `capability: 'none'`, and `screenshot: false`. */
function screenshotCapable(caps) {
  if (!caps) return null;
  if (caps.capability === 'none' || caps.screenshot === false) return false;
  return true;
}

export default function SurfaceWindow({ view, desktopId }) {
  const [mode, setMode] = useState('auto');
  const [caps, setCaps] = useState(null);
  const [probeError, setProbeError] = useState(null);
  const [attempt, setAttempt] = useState(0);

  // Probed once per mount: it drives auto mode and whether Screen is offered.
  useEffect(() => {
    let cancelled = false;
    setCaps(null);
    setProbeError(null);
    desktopsApi
      .surfaceCapabilities(desktopId, view.id)
      .then((answer) => {
        if (!cancelled) setCaps(answer);
      })
      .catch((problem) => {
        if (!cancelled) setProbeError(problem);
      });
    return () => {
      cancelled = true;
    };
  }, [desktopId, view.id, attempt]);

  if (!view.available) {
    return (
      <div className="window-state" role="status">
        <p>
          {view.unavailableReason === 'reinstalled'
            ? 'This app was reinstalled. Open the surface again to use this window.'
            : 'This app is no longer installed.'}
        </p>
      </div>
    );
  }

  const capable = screenshotCapable(caps);
  const effective =
    mode === 'screen' || mode === 'desktop'
      ? mode
      : !caps
        ? null
        : capable === false
          ? 'desktop'
          : 'screen';

  return (
    <div className="surface-window">
      <div className="surface-window-toolbar">
        <SegControl
          label="How to look at this surface"
          options={MODES}
          value={mode}
          onChange={setMode}
        />
        {caps && capable === false ? (
          <span className="surface-window-note">
            {caps.reason || 'This surface cannot send pictures, so it is drawn as a desktop.'}
          </span>
        ) : null}
      </div>
      <div className="surface-window-body">
        {probeError ? (
          <div className="window-state" role="alert">
            <p>{probeError.message}</p>
            <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
              Retry
            </button>
          </div>
        ) : effective === null ? (
          <div className="window-state" role="status">
            <p>Asking what this surface can do…</p>
          </div>
        ) : effective === 'screen' ? (
          <ScreenMode view={view} desktopId={desktopId} />
        ) : (
          <DesktopMode view={view} desktopId={desktopId} />
        )}
      </div>
    </div>
  );
}

/** The document, polled and drawn. `refresh.every` is a floor: the window
 *  may poll less often, never more. */
function DesktopMode({ view, desktopId }) {
  const [document_, setDocument] = useState(null);
  const [error, setError] = useState(null);
  const { views } = useDesktops();
  const titled = useRef(null);

  useEffect(() => {
    let stopped = false;
    let timer = null;
    let interval = DEFAULT_POLL_MS;
    const tick = async () => {
      if (stopped) return;
      // A window nobody is looking at does not need a fresh document; the
      // next visible moment polls again.
      if (document.visibilityState === 'visible') {
        try {
          const next = await desktopsApi.surface(desktopId, view.id);
          if (stopped) return;
          setDocument(next);
          setError(null);
          // The surface names its own floor. Never faster than it asked for.
          const every = Number(next?.refresh?.every);
          interval = Number.isFinite(every) && every > 0 ? every * 1000 : DEFAULT_POLL_MS;
          // The document's title is the hostname: it becomes the window's
          // title, once, when it differs.
          const title = typeof next?.title === 'string' ? next.title : '';
          if (title && title !== view.title && titled.current !== title) {
            titled.current = title;
            desktopsApi
              .updateView(desktopId, view.id, { title })
              .then(() => views?.refresh?.())
              .catch(() => {});
          }
        } catch (problem) {
          if (!stopped) setError(problem);
        }
      }
      if (!stopped) timer = setTimeout(tick, interval);
    };
    tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
    // `views` is read only for the refresh after a title change; depending on
    // it would restart the poll on every window move.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [desktopId, view.id, view.title]);

  if (error && !document_) {
    return (
      <div className="window-state" role="alert">
        <p>{error.message}</p>
      </div>
    );
  }
  if (!document_) {
    return (
      <div className="window-state" role="status">
        <p>Asking for the surface…</p>
      </div>
    );
  }
  return (
    <WidgetBoundary widgetType="surface">
      <SurfaceView surface={document_} />
    </WidgetBoundary>
  );
}

/** A picture of the surface, once a second, with how old it is once that is
 *  worth saying — the same honesty the agent's remote view shows. */
function ScreenMode({ view, desktopId }) {
  const [frame, setFrame] = useState(null);
  const [error, setError] = useState(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let stopped = false;
    let timer = null;
    let inFlight = false;
    const tick = async () => {
      if (stopped) return;
      if (document.visibilityState === 'visible' && !inFlight) {
        inFlight = true;
        try {
          const next = await desktopsApi.surfaceFrame(desktopId, view.id);
          if (stopped) return;
          setFrame(next);
          setError(null);
          setNow(Date.now());
        } catch (problem) {
          if (!stopped) setError(problem);
        } finally {
          inFlight = false;
        }
      }
      if (!stopped) timer = setTimeout(tick, FRAME_MS);
    };
    tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [desktopId, view.id]);

  // The staleness note ages between frames even when the producer's clock
  // does not move.
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const captured = frame?.captured_at ? new Date(frame.captured_at).getTime() : 0;
  const stale = captured && now - captured > 2500 ? Math.round((now - captured) / 1000) : 0;

  if (error && !frame) {
    return (
      <div className="window-state" role="alert">
        <p>{error.message}</p>
      </div>
    );
  }
  return (
    <div className="surface-screen">
      {frame ? (
        <img
          className="surface-screen-frame"
          src={`data:image/${frame.format === 'png' ? 'png' : 'jpeg'};base64,${frame.image_base64}`}
          alt=""
          width={frame.width}
          height={frame.height}
          draggable={false}
        />
      ) : (
        <div className="window-state" role="status">
          <p>Waiting for a picture of the surface…</p>
        </div>
      )}
      {stale ? (
        <p className="remote-stale" role="status">
          This picture is {stale} seconds old.
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="surface-screen-error">
          {error.message}
        </p>
      ) : null}
    </div>
  );
}
