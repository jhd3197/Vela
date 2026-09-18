// What a token is, what a theme may set, and how everything else is derived
// from it. This module holds no colours of its own: the stock values live in
// `theme.vela.json`, and this file says what they mean.
//
// Origin: ServerKit `frontend/src/data/themeTokens.js` (MIT, same owner) — the
// three-table shape (whitelist, aliases, derived) and the value rules. What is
// different: ServerKit let a theme set every step of every ramp, so a theme was
// eighty values and a bad one was eighty ways to be unreadable. Here a theme
// sets one base colour per role and the ramp is derived, so a theme is small
// and a step of one role always matches the same step of another.
//
// `web/scripts/build-tokens.mjs` writes `_tokens.scss` from this. The runtime
// applier (`apply.js`) walks the same tables, and the build exports the
// whitelist to `vela/assets/theme-tokens.json` so the server validates against
// this list rather than a second copy of it.
import { contrast, firstLegible, flatten, mix, parse, ramp, withAlpha } from './color.js';

export const SCHEMA_VERSION = 1;

/**
 * The nine shared lightness stops, in OKLCH L.
 *
 * Measured from the Nocturne neutral ramp the current token sheet already
 * carries (#f3f5fe through #292b31) and rounded to three places. Every role
 * uses these, which is what makes step 600 of the accent and step 600 of the
 * neutral read at the same weight.
 */
export const RAMP_STOPS = [0.971, 0.93, 0.869, 0.779, 0.68, 0.579, 0.48, 0.38, 0.29];
export const RAMP_STEPS = [100, 200, 300, 400, 500, 600, 700, 800, 900];

/** The roles that carry a 100-900 ramp. The rest are single values. */
export const RAMPED_ROLES = ['neutral', 'accent', 'cyan'];

/**
 * Every token a theme may set, grouped, with the validator each value has to
 * pass. The groups are the order the generated sheet writes them in.
 */
export const CANONICAL = {
  ground: [
    '--bg',
    '--bg-rail',
    '--bg-workspace',
    '--bg-panel',
    '--bg-header',
    '--bg-card',
    '--bg-field',
    '--bg-inset',
    '--bg-pop',
  ],
  text: ['--text', '--text-dim', '--text-faint'],
  border: ['--border', '--border-strong'],
  role: ['--neutral', '--accent', '--cyan', '--green', '--amber', '--red'],
  radius: ['--radius-sm', '--radius-md', '--radius-lg'],
  font: ['--font', '--mono'],
  shadow: ['--shadow-sm', '--shadow-md', '--shadow-lg'],
  glow: ['--glow'],
  // What a wallpaper is seen through. A photograph is not interface colour and
  // is not a theme's to choose, but the veil over it and the glass that floats
  // on it are: without these a stylesheet has to write a near-black by hand,
  // and an applied theme leaves the desk looking like the one it replaced.
  wall: ['--scrim', '--glass'],
  // Ink that sits on something the surface tokens do not describe: a
  // photograph, or a button flooded with the accent. Both are the one place
  // `--text` is the wrong answer, and both are a theme's to decide -- a theme
  // with a pale accent needs dark ink on its filled buttons.
  ink: ['--on-wall', '--on-accent'],
};

/** The kind of value each group holds, which is what the server validates. */
export const GROUP_TYPES = {
  ground: 'color',
  text: 'color',
  border: 'color',
  role: 'color',
  radius: 'length',
  font: 'font',
  shadow: 'shadow',
  glow: 'gradient',
  wall: 'color',
  ink: 'color',
};

/** Flat, in the order the sheet writes them. */
export const CANONICAL_TOKENS = Object.values(CANONICAL).flat();

/** `--bg` → `ground`, for the validator and the error message. */
export const TOKEN_GROUP = Object.fromEntries(
  Object.entries(CANONICAL).flatMap(([group, names]) => names.map((name) => [name, group])),
);

/**
 * Font stacks a theme may name. Nothing else is accepted, and nothing is ever
 * fetched: a theme that wants a face Vela does not already load would have to
 * make a network request, and a theme never does.
 */
export const FONT_ALLOW_LIST = [
  "'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif",
  "system-ui, -apple-system, 'Segoe UI', sans-serif",
  "Georgia, 'Times New Roman', serif",
  "ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace",
];

/**
 * Legacy name → the canonical name it now reads from. Nothing is removed in
 * this plan: the stylesheet is ten thousand lines and these names are its
 * vocabulary. The generator emits both and the runtime applier expands the
 * same way, so a theme that sets `--radius-md` moves `--radius` too.
 */
export const ALIASES = {
  '--radius': '--radius-md',
  '--shadow-card': '--shadow-md',
  '--shadow-pop': '--shadow-lg',
};

/**
 * Sizes, rhythm and timing. System constants: a theme changes what Vela is
 * coloured with, never how densely it is set. `--space-*` is Nocturne's 0.7
 * density rounded to whole pixels.
 */
export const SCALES = {
  space: {
    '--space-1': '3px',
    '--space-2': '6px',
    '--space-3': '8px',
    '--space-4': '11px',
    '--space-5': '14px',
    '--space-6': '17px',
    '--space-7': '22px',
    '--space-8': '28px',
  },
  type: {
    '--text-xs': '11.5px',
    '--text-sm': '12.5px',
    '--text-md': '14px',
    '--text-base': '15px',
    '--text-lg': '17px',
    '--text-xl': '20px',
    '--text-2xl': '25px',
    '--text-3xl': '32px',
  },
  motion: {
    '--motion-fast': '120ms',
    '--motion-base': '200ms',
    '--motion-slow': '320ms',
    '--ease': 'cubic-bezier(0.22, 0.61, 0.36, 1)',
  },
  // Dimensions, not rhythm: the rail is as wide as its controls need to be and
  // a theme has no opinion about it. Kept here so the generated sheet still
  // carries every name the stylesheet reads.
  layout: {
    '--rail-w': '62px',
    '--rail-control': '42px',
    '--panel-w': '252px',
    '--header-pad': '11px 20px',
    '--content-pad': '22px 26px',
  },
};

/**
 * How much of a role shows through in its soft tint. Per role rather than one
 * number because these are the values the stylesheet was tuned with: the red
 * wash behind a failed row is lighter than the green one behind a healthy one
 * on purpose, so a failure reads as a tint and not as a block of colour.
 */
export const SOFT_ALPHA = { green: 0.12, amber: 0.12, red: 0.1, accent: 0.12 };

/**
 * The veil a wallpaper is read through, per base: the stops are geometry and
 * fixed, the colour is the theme's `--scrim`.
 *
 * This is derived here rather than written in `_desk.scss` with `var()` stops
 * because a gradient whose stops come from a custom property loses Chrome's
 * dithering: the same colours, but banded across a photograph. Emitted with
 * literal stops, it renders exactly as the hand-written one did and still
 * follows the theme, which is what a derived token is for.
 */
export const VEIL = {
  light: { angle: '100deg', stops: [[0.5], [0.14, '40%'], [0.06, '62%'], [0.34]] },
  dark: { angle: '100deg', stops: [[0.72], [0.42, '40%'], [0.34, '62%'], [0.6]] },
};

/** The same veil with the dimming turned off: present, but barely. */
export const VEIL_PLAIN = {
  light: { angle: '100deg', stops: [[0.24], [0.04, '45%'], [0.16]] },
  dark: { angle: '100deg', stops: [[0.42], [0.08, '45%'], [0.28]] },
};

/** The colours the stylesheet also needs as bare channels, for `rgba(…)`. */
export const CHANNEL_TOKENS = ['--scrim', '--glass', '--on-wall'];

/** Which ramp step carries the accent's line weight, per base. */
const LINE_STEP = { light: 300, dark: 500 };

const stepIndex = (step) => RAMP_STEPS.indexOf(step);

/**
 * Every token the browser actually sets, from the canonical ones a theme
 * carries: the ramps, the tints, the nav-active trio and the aliases.
 *
 * Deterministic and pure — the build script and the runtime applier both call
 * this, so what CI generates and what a browser derives from an imported theme
 * cannot disagree.
 */
export function derive(tokens, base) {
  const out = { ...tokens };
  const at = (role, step) => out[`--${role}-${step}`];

  for (const role of RAMPED_ROLES) {
    const source = tokens[`--${role}`];
    if (!source) continue;
    ramp(source, RAMP_STOPS).forEach((value, index) => {
      out[`--${role}-${RAMP_STEPS[index]}`] = value;
    });
  }

  // A soft tint stays translucent rather than being flattened onto a surface:
  // the same pill sits on a card, on a field and over a wallpaper, and a
  // flattened tint would show a seam on two of the three.
  for (const role of ['green', 'amber', 'red']) {
    if (tokens[`--${role}`])
      out[`--${role}-soft`] = withAlpha(tokens[`--${role}`], SOFT_ALPHA[role]);
  }
  if (tokens['--accent']) {
    out['--accent-soft'] = withAlpha(tokens['--accent'], SOFT_ALPHA.accent);
    // `--accent-strong` is the one accent value that has to be read as text,
    // so it is not a fixed step: the ramp is walked from the base downwards
    // (upwards on a dark ground) until a step clears 4.5 against the tint it
    // sits on. That is why the stock light theme's violet cannot be its own
    // strong value and the dark theme's lavender can.
    // Measured against the tint over the *ground*, not over a card. The same
    // pill sits on a card, on the workspace and on a field, and the ground is
    // the darkest of them on a light base: a step chosen against white clears
    // 4.5 there and misses it two surfaces over.
    const surface = mix(tokens['--accent'], tokens['--bg'] || '#ffffff', SOFT_ALPHA.accent);
    const order =
      base === 'dark'
        ? RAMP_STEPS.slice(0, stepIndex(500) + 1).reverse()
        : RAMP_STEPS.slice(stepIndex(500));
    out['--accent-strong'] = firstLegible(
      order.map((step) => at('accent', step)),
      surface,
    );
    out['--accent-line'] = at('accent', LINE_STEP[base] ?? 300);
    out['--nav-active-text'] = out['--accent-strong'];
    out['--nav-active-bg'] = out['--accent-soft'];
    out['--nav-active-bar'] = tokens['--accent'];
  }

  // Channel companions for the three colours that are almost always drawn at
  // an alpha: `rgba(var(--scrim-rgb), 0.5)` is the form the stylesheet already
  // used, so a veil over a photograph composites and interpolates exactly as it
  // did. `color-mix()` reaches the same colour but not the same gradient --
  // Chrome interpolates the mixed stops differently, and over a wallpaper that
  // shows as a shift across the whole picture.
  for (const name of CHANNEL_TOKENS) {
    const value = tokens[name];
    if (!value) continue;
    const rgb = parse(value);
    if (rgb) out[`${name}-rgb`] = [rgb.r, rgb.g, rgb.b].map((c) => Math.round(c * 255)).join(', ');
  }

  const veil = (recipe) =>
    `linear-gradient(${recipe.angle}, ` +
    recipe.stops
      .map(([opacity, position]) =>
        [withAlpha(tokens['--scrim'], opacity), position].filter(Boolean).join(' '),
      )
      .join(', ') +
    ')';
  if (tokens['--scrim'] && VEIL[base]) {
    out['--veil'] = veil(VEIL[base]);
    out['--veil-plain'] = veil(VEIL_PLAIN[base]);
  }

  for (const [alias, canonical] of Object.entries(ALIASES)) {
    if (out[canonical] !== undefined) out[alias] = out[canonical];
  }
  return out;
}

/** The six values Personalise draws a theme's swatch strip from. */
export const SWATCH_TOKENS = ['--bg', '--bg-card', '--accent', '--cyan', '--text', '--border'];

/**
 * The pairs `assert-themes.mjs` and the in-browser check measure. `min` is 4.5
 * where the pair is text on a surface and 3 where it is chrome: an icon, a
 * line or an active indicator, which the eye finds by shape as well as value.
 */
export const CONTRAST_PAIRS = [
  ...['--bg-card', '--bg-workspace', '--bg-panel', '--bg-pop', '--bg-field'].flatMap((surface) =>
    ['--text', '--text-dim', '--text-faint'].map((ink) => ({ ink, surface, min: 4.5 })),
  ),
  { ink: '--accent', surface: '--bg-card', min: 3 },
  { ink: '--cyan', surface: '--bg-rail', min: 3 },
  { ink: '--accent-strong', surface: '--accent-soft', min: 4.5 },
  { ink: '--border', surface: '--bg-card', min: 1.3 },
];

/** Measure one derived token set. Returns every pair that did not clear. */
export function checkContrast(derived) {
  const ground = derived['--bg'];
  const failures = [];
  for (const pair of CONTRAST_PAIRS) {
    const ink = derived[pair.ink];
    const surface = derived[pair.surface];
    if (!ink || !surface || !ground) continue;
    // A translucent surface is read over the ground behind it, which is what
    // the eye does: the rail is a wash over `--bg`, not a colour of its own.
    const ratio = contrast(ink, flatten(surface, ground));
    if (ratio < pair.min) failures.push({ ...pair, ratio: Math.round(ratio * 100) / 100 });
  }
  return failures;
}
