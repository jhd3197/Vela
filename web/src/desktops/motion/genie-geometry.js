// Where every band of a window is, at any moment of the warp.
//
// Pure arithmetic, no canvas, no clock, no React. That separation is the point:
// the shape of this motion is the part somebody tuned and approved, and it has
// to be checkable at 0%, 25%, 50%, 75% and 100% without rendering anything. A
// geometry that could only be inspected by watching it is a geometry nobody can
// say is right.
//
// The model, in one paragraph. The window image is drawn as 140 overlapping
// horizontal bands — not 140 elements, 140 draws of one image. Collapse
// progress `c` runs 0 (window) to 1 (icon). Each band has its own progress,
// running ahead by an amount proportional to how close it already is to the
// destination; that head start is the *neck*. A band narrows as it goes, swings
// sideways by a signed amount that peaks halfway, and lands inside the icon.
//
// Everything here is in CSS pixels, in the overlay's coordinate space. The
// device pixel ratio belongs to the canvas backing store and is applied by the
// renderer, not smuggled into the layout.

import {
  BAND_COUNT,
  BAND_OVERLAP_PX,
  COLLAPSE_EXPONENT,
  EXPAND_EXPONENT,
  GENIE,
  MIN_BAND_HEIGHT_PX,
  MIN_BAND_WIDTH_PX,
  WIDTH_COLLAPSE,
  leadFor,
} from './genie-preset.js';

const clamp01 = (value) => Math.min(1, Math.max(0, value));

/**
 * How far through the collapse we are, from a clock.
 *
 * `0` is the window and `1` is the icon, for both directions — the direction
 * decides which end the clock starts at, not what the numbers mean. Keeping one
 * meaning for `c` is what lets the band arithmetic below be written once.
 *
 * The two easings are the reference's own and are part of the approved feel.
 * They are deliberately not a shared cubic-bezier: collapsing accelerates into
 * the icon and expanding decelerates out of it, and those are different curves.
 */
export function collapseAt(
  elapsedMs,
  { durationMs = GENIE.durationMs, direction = 'collapse' } = {},
) {
  const k = durationMs > 0 ? clamp01(elapsedMs / durationMs) : 1;
  if (direction === 'expand') {
    // The reference's expansion easing is `1 - (1-k)^2.2` measured from the
    // icon outwards; `c` counts the other way, so this is its complement.
    return clamp01((1 - k) ** EXPAND_EXPONENT);
  }
  return clamp01(k ** COLLAPSE_EXPONENT);
}

/**
 * How long a reversal should take.
 *
 * A window a quarter of the way into collapsing, told to come back, has a
 * quarter of the distance to travel — so it takes a quarter of the time. The
 * full-distance preset stays 480 ms; nothing here shortens that.
 */
export function reversalDuration(collapse, { durationMs = GENIE.durationMs, direction } = {}) {
  const remaining = direction === 'expand' ? collapse : 1 - collapse;
  return Math.max(60, Math.round(durationMs * clamp01(remaining)));
}

/**
 * The elapsed time that corresponds to a collapse value, for starting mid-way.
 *
 * The inverse of `collapseAt`, so a reversal can begin at the exact shape that
 * is already on screen rather than jumping to an endpoint and easing from
 * there.
 */
export function elapsedFor(
  collapse,
  { durationMs = GENIE.durationMs, direction = 'collapse' } = {},
) {
  const c = clamp01(collapse);
  const k = direction === 'expand' ? 1 - c ** (1 / EXPAND_EXPONENT) : c ** (1 / COLLAPSE_EXPONENT);
  return clamp01(k) * durationMs;
}

/**
 * One band's own progress, given the whole effect's.
 *
 * `u` is how far this band is from the destination, normalized so the nearest
 * band is 0 and the furthest is 1. The nearest band runs on the whole timeline;
 * the furthest waits `lead` of it before starting. That staggering, and nothing
 * else, is what produces the neck.
 */
export function bandProgress(collapse, u, lead = leadFor()) {
  const denominator = 1 - lead;
  if (denominator <= 0) return clamp01(collapse);
  return clamp01((collapse - lead * clamp01(u)) / denominator);
}

/**
 * Every band of the window, as source and destination rectangles.
 *
 * The result feeds `drawImage` directly: `{sx, sy, sw, sh}` reads from the
 * captured frame and `{dx, dy, dw, dh}` writes onto the overlay. A renderer
 * that wanted to draw this some other way could, which is the reason the two
 * are kept apart.
 *
 * `source` is where the window is on screen and `target` is the icon it is
 * going to, both in overlay coordinates. `frame` is the size of the captured
 * image, which is not always the size of the window — a frame from a remote
 * view is whatever that view's viewport was.
 */
export function bandsFor({
  source,
  target,
  frame,
  collapse,
  direction = 'collapse',
  swoopPx = GENIE.swoopPx,
  neck = GENIE.neck,
  bandCount = BAND_COUNT,
}) {
  if (!source || !target || !frame?.width || !frame?.height) return [];
  const c = clamp01(collapse);
  const lead = leadFor(neck);
  // Collapsing swings one way and expanding swings the other. The source does
  // this deliberately: replaying one path backwards looks like a rewind, and
  // the two directions are supposed to feel like different movements.
  const sign = direction === 'expand' ? -1 : 1;

  const anchorY = target.y + target.height / 2;
  const rows = [];
  let furthest = 0;
  for (let index = 0; index < bandCount; index += 1) {
    const share = (index + 0.5) / bandCount;
    const centreY = source.y + source.height * share;
    const distance = Math.abs(centreY - anchorY);
    if (distance > furthest) furthest = distance;
    rows.push({ index, share, centreY, distance });
  }
  const span = furthest || 1;

  const bands = [];
  for (const row of rows) {
    const u = row.distance / span;
    const progress = bandProgress(c, u, lead);

    const width = Math.max(MIN_BAND_WIDTH_PX, source.width * (1 - WIDTH_COLLAPSE * progress));
    const shrunk = target.width + (source.width - target.width) * (1 - progress);
    // The band's left edge travels from the window's to the icon's, and its
    // width collapses on the way. Both terms use the band's own progress, so a
    // band that has not started yet has not moved and has not narrowed.
    const left = source.x + (target.x - source.x) * progress;
    const swoop = sign * swoopPx * Math.sin(Math.PI * progress) * u;

    const height = Math.max(
      MIN_BAND_HEIGHT_PX,
      (source.height + (target.height - source.height) * progress) / bandCount + BAND_OVERLAP_PX,
    );
    const top =
      row.centreY + (target.y + target.height * row.share - row.centreY) * progress - height / 2;

    bands.push({
      index: row.index,
      progress,
      u,
      sx: 0,
      sy: (frame.height * row.index) / bandCount,
      sw: frame.width,
      sh: Math.max(1, frame.height / bandCount + BAND_OVERLAP_PX),
      dx: left + swoop,
      dy: top,
      dw: Math.min(width, Math.max(MIN_BAND_WIDTH_PX, shrunk)),
      dh: height,
    });
  }
  return bands;
}

/**
 * The rectangle the whole effect currently occupies, for clipping.
 *
 * Used to keep the overlay's painting inside the area it is allowed to touch,
 * and to know when nothing needs drawing at all.
 */
export function bandsExtent(bands) {
  if (!bands.length) return null;
  let left = Infinity;
  let top = Infinity;
  let right = -Infinity;
  let bottom = -Infinity;
  for (const band of bands) {
    left = Math.min(left, band.dx);
    top = Math.min(top, band.dy);
    right = Math.max(right, band.dx + band.dw);
    bottom = Math.max(bottom, band.dy + band.dh);
  }
  return { x: left, y: top, width: right - left, height: bottom - top };
}
