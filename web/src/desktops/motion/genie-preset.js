// The approved motion, as one constant.
//
// There is exactly one of these and everything reads it: the renderer, the
// geometry, the tests, the documentation. The prototype this came from carried
// swoop 10 in three separate places — its metadata, its draw fallback and the
// timing it displayed — and changing one of them would have left a preset that
// was right in the label and wrong in the motion. Hence a single frozen object
// with no second copy to drift from.
//
// `neck` is the prototype's own dimensionless control. It is not pixels and not
// milliseconds: it becomes a *lead*, the fraction of the whole duration by which
// the rows nearest the destination start ahead of the rows furthest from it, and
// that lead is what makes the shape narrow before it travels.
//
// `swoopPx` is 150 CSS pixels at the reference composition's scale, which is
// 1440 × 900. It is a distance, so it stays a distance.

/** The user-approved normal-motion preset. Versioned, so a change is visible. */
export const GENIE = Object.freeze({
  preset: 'vela-genie-v1',
  durationMs: 480,
  neck: 10,
  swoopPx: 150,
});

/** The composition the swoop distance was tuned against. */
export const REFERENCE = Object.freeze({ width: 1440, height: 900 });

/** How many overlapping horizontal bands the window image is drawn as. */
export const BAND_COUNT = 140;

/**
 * Bands are drawn slightly taller than their share so their edges overlap.
 *
 * Without it, rounding puts a sub-pixel gap between neighbours and the window
 * comes apart into visible stripes as it bends. Revalidated under device-pixel
 * scaling rather than assumed to be one number forever.
 */
export const BAND_OVERLAP_PX = 0.9;

/** No band is ever drawn thinner than this, whatever the arithmetic says. */
export const MIN_BAND_HEIGHT_PX = 0.5;

/** How far a band's width collapses at full progress. From the reference. */
export const WIDTH_COLLAPSE = 0.955;

/** The narrowest a band gets, so something is still painted at the end. */
export const MIN_BAND_WIDTH_PX = 2;

/** The reference easings. Part of the approved feel, not a placeholder. */
export const COLLAPSE_EXPONENT = 1.7;
export const EXPAND_EXPONENT = 2.2;

/**
 * The lead the neck control produces.
 *
 * `neck / 100`, held inside a range where the effect is still recognisable: at
 * zero every row moves together and there is no neck at all; at one the last row
 * never starts.
 */
export function leadFor(neck = GENIE.neck) {
  const value = Number.isFinite(neck) ? neck / 100 : GENIE.neck / 100;
  return Math.min(0.92, Math.max(0.06, value));
}

/**
 * The swoop distance for a given work area.
 *
 * The baseline is the reference width. A narrower screen gets proportionally
 * less sideways travel, because 150 px of swing on a 390 px phone is most of
 * the screen. The clamp is explicit and tested rather than being folded into a
 * duration somewhere and forgotten.
 */
export function swoopFor(area, swoopPx = GENIE.swoopPx) {
  if (!area?.width || area.width >= REFERENCE.width) return swoopPx;
  return Math.round(swoopPx * (area.width / REFERENCE.width));
}
