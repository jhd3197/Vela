// Local app artwork. Each mark is drawn on a 96×96 grid in `currentColor`
// with a consistent stroke weight, so one tile treatment can own colour and
// sizing while every app keeps its own silhouette. Nothing here fetches an
// icon at runtime and nothing depends on an app's own files, so the marks
// render offline and identically for an installed app and a catalog listing.
//
// First-party marks follow the artwork each app already ships in its own
// repository, so an app looks the same in the hub as it does in its own
// window. Third-party services are identified by monogram: a user-added
// service is named by the person who added it, and its initial separates it
// from the others far better than a row of identical globes.

import { artworkKey } from '../appArtworkKey.js';

const STROKE = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 7,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
};

const THIN = { ...STROKE, strokeWidth: 6 };

// `scale` is the optical size of the mark inside its tile. A wide mark needs
// a smaller box than a compact one to look like the same weight beside it.
const ART = {
  notes: {
    scale: 0.56,
    // Two binding rings rather than three: at rail size on a 1× display a
    // third ring is indistinguishable from the pad's top edge.
    draw: (
      <>
        <rect x="20" y="27" width="56" height="55" rx="9" {...STROKE} />
        <path d="M37 13v17M59 13v17" {...STROKE} />
        <path d="M34 50h28M34 65h18" {...THIN} />
      </>
    ),
  },
  meals: {
    scale: 0.56,
    draw: (
      <>
        <path d="M27 14v19c0 6 4 10 9 10s9-4 9-10V14" {...STROKE} />
        <path d="M36 16v17M36 43v39" {...STROKE} />
        <path d="M69 14c-6 5-9 15-9 23 0 7 4 11 9 11v34" {...STROKE} />
      </>
    ),
  },
  health: {
    scale: 0.58,
    draw: (
      <>
        <path
          d="M48 80C36 68 18 56 18 40c0-10 7-18 16-18 6 0 11 3 14 8 3-5 8-8 14-8 9 0 16 8 16 18 0 16-18 28-30 40z"
          {...STROKE}
        />
        <path d="M29 45h11l6-10 8 20 6-10h11" {...THIN} />
      </>
    ),
  },
  finance: {
    scale: 0.6,
    draw: (
      <>
        <path d="M18 68 38 47l14 10 26-28" {...STROKE} />
        <path d="M60 29h18v18" {...STROKE} />
        <path d="M18 84h60" {...THIN} opacity="0.45" />
      </>
    ),
  },
  'system-info': {
    scale: 0.58,
    // Two pins a side, not three. Twelve pins turn into a grey fringe once the
    // tile is small enough for the rail.
    draw: (
      <>
        <rect x="27" y="27" width="42" height="42" rx="8" {...STROKE} />
        <rect x="42" y="42" width="12" height="12" rx="3" fill="currentColor" stroke="none" />
        <path
          d="M40 13v14M56 13v14M40 69v14M56 69v14M13 40h14M13 56h14M69 40h14M69 56h14"
          {...STROKE}
        />
      </>
    ),
  },
  'hello-vela': {
    scale: 0.58,
    draw: (
      <>
        <path d="M46 14 46 58 20 58Z" {...STROKE} />
        <path d="M57 32v26h21Z" {...STROKE} />
        <path d="M14 68h70l-11 15H25Z" {...STROKE} />
      </>
    ),
  },
  ollama: {
    scale: 0.56,
    draw: (
      <>
        <rect x="20" y="20" width="56" height="17" rx="8" {...STROKE} />
        <rect x="20" y="59" width="56" height="17" rx="8" {...STROKE} />
        <path d="M31 48h34" {...STROKE} />
      </>
    ),
  },
};

// Category marks keep the same drawing language as the app marks, so an app
// the hub does not recognise still sits beside one it does.
const CATEGORY_ART = {
  productivity: {
    scale: 0.56,
    draw: (
      <>
        <rect x="21" y="18" width="54" height="64" rx="9" {...STROKE} />
        <path d="M34 38h28M34 52h28M34 66h18" {...THIN} />
      </>
    ),
  },
  wellness: ART.health,
  lifestyle: ART.meals,
  finance: ART.finance,
  utilities: {
    scale: 0.56,
    draw: (
      <>
        <rect x="16" y="22" width="64" height="22" rx="7" {...STROKE} />
        <rect x="16" y="52" width="64" height="22" rx="7" {...STROKE} />
        <path d="M30 33h.01M30 63h.01" {...STROKE} />
      </>
    ),
  },
  'getting-started': ART['hello-vela'],
  developer: ART.ollama,
  connected: {
    scale: 0.58,
    draw: (
      <>
        <circle cx="48" cy="48" r="31" {...STROKE} />
        <ellipse cx="48" cy="48" rx="14" ry="31" {...THIN} />
        <path d="M18 36h60M18 60h60" {...THIN} />
      </>
    ),
  },
};

// The generic mark is filled rather than stroked. Four outlined squares have
// four gaps as thin as their strokes, and on a 1× display at rail size those
// gaps close up and the grid reads as two solid bars.
const UNKNOWN = {
  scale: 0.56,
  draw: (
    <>
      <rect x="11" y="11" width="30" height="30" rx="8" fill="currentColor" />
      <rect x="55" y="11" width="30" height="30" rx="8" fill="currentColor" />
      <rect x="11" y="55" width="30" height="30" rx="8" fill="currentColor" />
      <rect x="55" y="55" width="30" height="30" rx="8" fill="currentColor" />
    </>
  ),
};

function monogramArt(letter) {
  return {
    scale: 0.62,
    monogram: true,
    draw: (
      <text
        x="48"
        y="48"
        textAnchor="middle"
        dominantBaseline="central"
        fill="currentColor"
        stroke="none"
        fontSize="58"
        fontWeight="600"
        fontFamily="var(--font)"
        letterSpacing="-1"
      >
        {letter}
      </text>
    ),
  };
}

// `artworkKey` owns the decision; this only turns the answer into a drawing,
// and falls back to the generic mark if a key ever has no artwork behind it.
export function appArtwork(app) {
  const resolved = artworkKey(app);
  if (resolved.kind === 'id') return ART[resolved.key] || UNKNOWN;
  if (resolved.kind === 'monogram') return monogramArt(resolved.key);
  if (resolved.kind === 'category') return CATEGORY_ART[resolved.key] || UNKNOWN;
  return UNKNOWN;
}

export { ART, CATEGORY_ART, UNKNOWN };
