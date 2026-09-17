// The screen the agent is actually using.
//
// Watching is passive and stays passive. A click on the picture does nothing
// until somebody has taken control — because a person resting a hand on a
// trackpad while reading should not type into a form the agent is halfway
// through filling in, and because "did I just do that?" is the worst question
// an interface can leave somebody asking.
//
// When control has been taken, the picture becomes an input surface: clicks and
// the allowed keys are mapped through the displayed box into the view's own
// coordinates and sent to the window they were aimed at. Nothing is read from
// the operating system's clipboard; text is what the person types here.
import { useCallback, useEffect, useRef, useState } from 'react';
import Button from '../components/ui/Button.jsx';
import { desktopsApi } from './desktopsApi.js';
import { toSendableKey, toViewPoint } from './input-coordinates.js';

/** How often to ask for a new picture while watching, and while controlling. */
const WATCH_MS = 900;
const CONTROL_MS = 350;

export default function RemoteView({ desktopId, views, selectedViewId }) {
  const [frame, setFrame] = useState(null);
  // The picture itself, as a blob URL this component owns and revokes. It is
  // fetched rather than linked because an <img src> cannot carry the bearer,
  // and a credential in a URL is a credential in somebody's history.
  const [picture, setPicture] = useState(null);
  const [lease, setLease] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const surface = useRef(null);
  const viewId =
    selectedViewId || views?.find((view) => view.agentViewable && view.available)?.id || null;

  // A picture, as often as watching needs and no more. Two people watching the
  // same desktop share one capture: being watched should not slow the agent.
  useEffect(() => {
    if (!viewId) return undefined;
    let stopped = false;
    let timer = null;
    const tick = async () => {
      if (stopped) return;
      if (document.visibilityState === 'visible') {
        try {
          const next = await desktopsApi.frame(desktopId, viewId, lease ? 200 : 800);
          if (stopped) return;
          setFrame((current) => {
            if (current?.digest === next.digest) return current;
            desktopsApi
              .frameBytes(desktopId, viewId, next.digest)
              .then((url) => {
                if (stopped) {
                  URL.revokeObjectURL(url);
                  return;
                }
                setPicture((previous) => {
                  if (previous) URL.revokeObjectURL(previous);
                  return url;
                });
              })
              .catch(() => {});
            return next;
          });
          setError(null);
        } catch (problem) {
          if (!stopped) setError(problem);
        }
      }
      if (!stopped) timer = setTimeout(tick, lease ? CONTROL_MS : WATCH_MS);
    };
    tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [desktopId, viewId, lease]);

  // The last picture goes with the component.
  useEffect(() => () => picture && URL.revokeObjectURL(picture), [picture]);

  // A closed tab does not give control back, and it does not need to: the lease
  // expires on its own. Trying to release it on the way out would mean a
  // credentialled request from a page that is being torn down, and the failure
  // mode of *that* is a lease nobody released and nobody noticed. The timeout is
  // the mechanism; this comment is here so the absence looks deliberate.

  const send = useCallback(
    async (input) => {
      if (!lease || !viewId) return;
      try {
        await desktopsApi.sendInput(desktopId, lease.leaseId, {
          ...input,
          viewId,
          frameAt: frame?.capturedAt,
        });
        setError(null);
      } catch (problem) {
        setError(problem);
        // A lease that is gone is gone. Say so rather than letting somebody
        // keep clicking at a window that is not listening.
        if (problem.status === 409) setLease(null);
      }
    },
    [desktopId, frame?.capturedAt, lease, viewId],
  );

  const take = async () => {
    setBusy(true);
    setError(null);
    try {
      setLease(await desktopsApi.takeOver(desktopId, viewId));
    } catch (problem) {
      setError(problem);
    } finally {
      setBusy(false);
    }
  };

  const give = async () => {
    if (!lease) return;
    setBusy(true);
    try {
      await desktopsApi.releaseControl(desktopId, lease.leaseId);
      setLease(null);
    } catch (problem) {
      setError(problem);
    } finally {
      setBusy(false);
    }
  };

  if (!viewId) {
    return <p className="field-hint">Nothing is open on this desktop to watch.</p>;
  }

  return (
    <section className="remote-view" aria-labelledby="remote-view-title">
      <header>
        <h3 id="remote-view-title">{lease ? 'You have control' : 'Watching'}</h3>
        <p className="field-hint">
          {lease
            ? 'Clicks and keys go to this window. The task stays paused until you say carry on.'
            : 'Clicking here does nothing. Take control first.'}
        </p>
      </header>

      <div
        ref={surface}
        className="remote-surface"
        data-control={lease ? 'yes' : 'no'}
        tabIndex={lease ? 0 : -1}
        role={lease ? 'application' : 'img'}
        aria-label={lease ? 'The agent’s window — you have control' : 'The agent’s window'}
        onClick={(event) => {
          if (!lease) return;
          const point = toViewPoint(event, surface.current, frame);
          if (point) send({ kind: 'click', point });
        }}
        onWheel={(event) => {
          if (!lease) return;
          send({ kind: 'scroll', dx: Math.round(event.deltaX), dy: Math.round(event.deltaY) });
        }}
        onKeyDown={(event) => {
          if (!lease) return;
          const key = toSendableKey(event);
          if (key) {
            event.preventDefault();
            send({ kind: 'key', key });
            return;
          }
          // One printable character at a time, typed by the person. Anything
          // that is not a character and not on the list never leaves this tab.
          if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
            event.preventDefault();
            send({ kind: 'text', text: event.key });
          }
        }}
      >
        {frame && picture ? (
          <img src={picture} alt="" width={frame.width} height={frame.height} draggable={false} />
        ) : (
          <p className="field-hint">Waiting for a picture of the window…</p>
        )}
      </div>

      {error && (
        <p role="alert" className="agent-blocked">
          {error.message}
        </p>
      )}

      <div className="form-actions">
        {lease ? (
          <Button size="small" pending={busy} onClick={give}>
            Give control back
          </Button>
        ) : (
          <Button size="small" variant="primary" pending={busy} onClick={take}>
            Take over
          </Button>
        )}
      </div>
    </section>
  );
}
