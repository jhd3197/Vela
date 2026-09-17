// The canvas the warp is painted on.
//
// One canvas, one window image, 140 draws of it per frame. Not 140 elements:
// the reference divides the picture into overlapping bands and redraws the same
// bitmap into each of them, which is what keeps it one continuous surface
// instead of a stack of strips that come apart at the corners.
//
// What this component is not allowed to be:
//
// - **In the way.** It never takes pointer input, never holds focus and is
//   hidden from assistive technology. It is a picture of something that is
//   happening; the real controls are underneath it and stay usable.
// - **A picture of the desktop.** It paints one view's frame and only that.
//   A whole-desktop capture would contain the owner's approval controls, which
//   are exactly what must never appear in anything the agent's side influences.
// - **The thing that decides.** The state machine decides; this draws. A frame
//   that arrives after the run it belonged to has ended is dropped.
//
// Canvas 2D and `requestAnimationFrame`, deliberately, until something measured
// says otherwise. The geometry is a separate module precisely so a different
// renderer could be put behind it if profiling ever justifies one.
import { useEffect, useRef } from 'react';
import { bandsFor, collapseAt } from './genie-geometry.js';
import { GENIE, swoopFor } from './genie-preset.js';

export default function GenieOverlay({ run, area, onDone, onProgress }) {
  const canvas = useRef(null);
  const raf = useRef(0);

  useEffect(() => {
    if (!run || !canvas.current) return undefined;
    const surface = canvas.current;
    const context = surface.getContext('2d');
    if (!context) {
      onDone?.(run.generation);
      return undefined;
    }

    // The backing store is sized for the display's density; every coordinate
    // below stays in CSS pixels. Mixing the two is how a window ends up drawn
    // at twice its intended size on a dense screen.
    const ratio = Math.min(3, Math.max(1, window.devicePixelRatio || 1));
    surface.width = Math.max(1, Math.round(area.width * ratio));
    surface.height = Math.max(1, Math.round(area.height * ratio));
    surface.style.width = `${area.width}px`;
    surface.style.height = `${area.height}px`;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);

    const swoopPx = swoopFor(area, GENIE.swoopPx);
    let cancelled = false;
    // A reversal begins at the shape that is already on screen rather than at
    // an endpoint, so the clock starts part-way through.
    const started = performance.now() - (run.startElapsed || 0);

    const paint = (now) => {
      if (cancelled) return;
      const elapsed = now - started;
      const collapse = collapseAt(elapsed, {
        durationMs: run.durationMs,
        direction: run.direction,
      });
      // Reported without re-rendering: a reversal has to know the shape it is
      // interrupting, and a state update per frame would cost more than the
      // animation does.
      onProgress?.(run.viewId, collapse);
      context.clearRect(0, 0, area.width, area.height);
      const bands = bandsFor({
        source: run.source,
        target: run.target,
        frame: { width: run.frame.width, height: run.frame.height },
        collapse,
        direction: run.direction,
        swoopPx,
      });
      for (const band of bands) {
        // A band that has collapsed past what the source can supply would ask
        // the canvas to read outside the image; skipping it is cheaper than
        // clamping every one of them every frame.
        if (band.sh <= 0 || band.dw <= 0 || band.dh <= 0) continue;
        context.drawImage(
          run.frame.image,
          band.sx,
          band.sy,
          band.sw,
          Math.min(band.sh, run.frame.height - band.sy),
          band.dx,
          band.dy,
          band.dw,
          band.dh,
        );
      }
      if (elapsed >= run.durationMs) {
        onDone?.(run.generation);
        return;
      }
      raf.current = requestAnimationFrame(paint);
    };

    raf.current = requestAnimationFrame(paint);

    // A hidden tab stops delivering animation frames, so the lifecycle cannot
    // depend on receiving the last one. Hiding settles the run immediately;
    // what is on screen when it comes back is the state that was decided, not a
    // half-collapsed window nobody can click.
    const onHidden = () => {
      if (document.visibilityState === 'hidden') onDone?.(run.generation);
    };
    document.addEventListener('visibilitychange', onHidden);

    return () => {
      cancelled = true;
      cancelAnimationFrame(raf.current);
      document.removeEventListener('visibilitychange', onHidden);
      context.clearRect(0, 0, area.width, area.height);
    };
  }, [area, onDone, onProgress, run]);

  if (!run) return null;
  return (
    <canvas
      ref={canvas}
      className="genie-overlay"
      aria-hidden="true"
      // Decoration, above the window it replaces and below everything the owner
      // needs: the rail, an approval, a dialog. It never intercepts a click.
      style={{ width: `${area.width}px`, height: `${area.height}px` }}
    />
  );
}
