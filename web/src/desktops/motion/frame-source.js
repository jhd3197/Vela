// Where a picture of a window comes from, and what to do when there isn't one.
//
// The honest starting point: **a web dashboard does not get a texture of every
// window on it.** An app runs in a cross-origin iframe, and a browser will not
// hand its pixels to the page around it — that restriction is the same one that
// stops any site reading any other, and it is not something to work around for
// decoration. Asking for screen recording, or weakening an app frame's sandbox,
// to make an animation prettier would be trading a real boundary for a visual.
//
// So this answers a capability question rather than promising an image. One
// surface can be captured today: a view the agent's managed browser is
// rendering, because Vela's own server already takes authorized frames of it for
// the viewer. Everything else gets the fallback, which is a restrained transform
// of the container Vela already owns — no pixels read, nothing to leak, and the
// window still goes where it is supposed to go.
//
// A frame is also never a free-floating image. It carries the view, the runtime
// session and the control generation it was taken under, and a frame whose
// identities do not match the window being animated is discarded rather than
// drawn: the one thing worse than no picture is a picture of something else.

import { desktopsApi } from '../desktopsApi.js';

/** What can be done for a given window. */
export const CAPABILITY = Object.freeze({
  /** A real authorized frame of the agent's view. Full warp. */
  REMOTE: 'remote',
  /** No readable pixels. The container moves instead. */
  FALLBACK: 'fallback',
});

/** How old a frame may be before it is a picture of a moment that has passed. */
export const MAX_FRAME_AGE_MS = 4000;

/**
 * Whether this window can be warped from a real picture of itself.
 *
 * Answered before anything starts, so the caller chooses a path rather than
 * discovering halfway through that there is nothing to draw.
 */
export function capabilityFor(view, { desktop } = {}) {
  if (!view) return { capability: CAPABILITY.FALLBACK, reason: 'there is no window here' };
  if (desktop?.kind !== 'agent' || !view.agentViewable) {
    return {
      capability: CAPABILITY.FALLBACK,
      // Said plainly rather than apologetically: this is a browser rule, not a
      // missing feature, and no amount of permission would change it.
      reason: 'a browser does not let this page read the pixels of a window it embeds',
    };
  }
  if (!view.available) {
    return { capability: CAPABILITY.FALLBACK, reason: 'that window needs reopening' };
  }
  return { capability: CAPABILITY.REMOTE, reason: null };
}

/**
 * Fetch a frame for one view, or say why there isn't one.
 *
 * The returned handle owns a blob URL and an `Image`, and `release()` is the
 * only correct way to be finished with it. A caller that dropped one would leak
 * a decoded bitmap per animation, which is the kind of leak that only shows up
 * after somebody has minimized a window four hundred times.
 */
export async function loadFrame(desktopId, view, { maxAgeMs = 400, signal } = {}) {
  let described;
  try {
    described = await desktopsApi.frame(desktopId, view.id, maxAgeMs);
  } catch (error) {
    return { ok: false, reason: error?.message || 'no picture of that window is available' };
  }
  if (signal?.aborted) return { ok: false, reason: 'cancelled' };
  // A frame belongs to one view in one browser lifetime. One from anywhere else
  // is not a stale picture of this window, it is a picture of another one.
  if (described.viewId !== view.id) {
    return { ok: false, reason: 'that picture is of a different window' };
  }
  if (Date.now() - described.capturedAt > MAX_FRAME_AGE_MS) {
    return { ok: false, reason: 'that picture is too old to animate from' };
  }

  let url;
  try {
    url = await desktopsApi.frameBytes(desktopId, view.id, described.digest);
  } catch (error) {
    return { ok: false, reason: error?.message || 'that picture is no longer available' };
  }
  if (signal?.aborted) {
    URL.revokeObjectURL(url);
    return { ok: false, reason: 'cancelled' };
  }

  const image = new Image();
  const loaded = await new Promise((resolve) => {
    image.onload = () => resolve(true);
    image.onerror = () => resolve(false);
    image.src = url;
  });
  if (!loaded || signal?.aborted) {
    URL.revokeObjectURL(url);
    return { ok: false, reason: loaded ? 'cancelled' : 'that picture could not be decoded' };
  }

  return {
    ok: true,
    capability: CAPABILITY.REMOTE,
    image,
    width: described.width,
    height: described.height,
    deviceScaleFactor: described.deviceScaleFactor,
    capturedAt: described.capturedAt,
    viewId: described.viewId,
    runtimeSessionId: described.runtimeSessionId,
    controlEpoch: described.controlEpoch,
    release() {
      image.src = '';
      URL.revokeObjectURL(url);
    },
  };
}
