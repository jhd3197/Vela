// The design system's colour maths: sRGB ↔ OKLCH, tonal ramps and WCAG
// contrast. One implementation, no dependencies, imported by the token
// generator, the runtime theme applier and the contrast gate — so a step the
// build writes into `_tokens.scss` and a step the browser derives from an
// imported theme are the same number.
//
// Origin: Björn Ottosson's OKLab reference matrices (public domain) and WCAG
// 2.x relative luminance. ServerKit's `ThemeContext.jsx` derived its hovers by
// darkening RGB channels; that is replaced here, because darkening in sRGB
// moves hue and chroma as well as lightness and the ramps have to line up
// across roles.

const clamp = (value, low, high) => (value < low ? low : value > high ? high : value);

/* ------------------------------------------------------------------ parsing */

const HEX = /^#([0-9a-f]{3,8})$/i;
const FUNC = /^rgba?\(([^)]+)\)$/i;

/** `#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa`, `rgb()`, `rgba()` to `{r,g,b,a}` 0-1. */
export function parse(value) {
  const text = String(value).trim();
  const hex = HEX.exec(text);
  if (hex) {
    let digits = hex[1];
    if (digits.length === 3 || digits.length === 4) {
      digits = [...digits].map((digit) => digit + digit).join('');
    }
    if (digits.length !== 6 && digits.length !== 8) return null;
    const byte = (index) => parseInt(digits.slice(index * 2, index * 2 + 2), 16) / 255;
    return { r: byte(0), g: byte(1), b: byte(2), a: digits.length === 8 ? byte(3) : 1 };
  }
  const func = FUNC.exec(text);
  if (func) {
    const parts = func[1].split(/[,/\s]+/).filter(Boolean);
    if (parts.length < 3) return null;
    const channel = (part) =>
      part.endsWith('%')
        ? clamp(parseFloat(part) / 100, 0, 1)
        : clamp(parseFloat(part) / 255, 0, 1);
    const opacity = parts[3] === undefined ? 1 : clamp(parseFloat(parts[3]), 0, 1);
    const [r, g, b] = parts.slice(0, 3).map(channel);
    if ([r, g, b, opacity].some((number) => !Number.isFinite(number))) return null;
    return { r, g, b, a: opacity };
  }
  return null;
}

const hexByte = (channel) =>
  Math.round(clamp(channel, 0, 1) * 255)
    .toString(16)
    .padStart(2, '0');

/** `{r,g,b,a}` 0-1 to `#rrggbb`, or `#rrggbbaa` when the alpha is not 1. */
export function toHex({ r, g, b, a = 1 }) {
  const base = `#${hexByte(r)}${hexByte(g)}${hexByte(b)}`;
  return a >= 1 ? base : `${base}${hexByte(a)}`;
}

/* -------------------------------------------------------------- conversions */

const toLinear = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const toGamma = (c) => (c <= 0.0031308 ? c * 12.92 : 1.055 * c ** (1 / 2.4) - 0.055);

/** sRGB 0-1 to OKLab. */
export function rgbToOklab({ r, g, b }) {
  const lr = toLinear(r);
  const lg = toLinear(g);
  const lb = toLinear(b);
  const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
  const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
  const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
  return {
    L: 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    a: 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    b: 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  };
}

/** OKLab to sRGB 0-1, unclamped so a caller can tell in-gamut from out. */
export function oklabToRgb({ L, a, b }) {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return {
    r: toGamma(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    g: toGamma(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    b: toGamma(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
  };
}

/** sRGB to `{L, C, H}` with hue in degrees. */
export function rgbToOklch(rgb) {
  const { L, a, b } = rgbToOklab(rgb);
  const C = Math.sqrt(a * a + b * b);
  let H = (Math.atan2(b, a) * 180) / Math.PI;
  if (H < 0) H += 360;
  return { L, C, H };
}

export function oklchToRgb({ L, C, H }) {
  const radians = (H * Math.PI) / 180;
  return oklabToRgb({ L, a: Math.cos(radians) * C, b: Math.sin(radians) * C });
}

/** A colour string to `{L, C, H}`, or null when it is not a colour this parses. */
export function toOklch(value) {
  const rgb = parse(value);
  return rgb ? rgbToOklch(rgb) : null;
}

const EPSILON = 1 / 512;
const inGamut = ({ r, g, b }) =>
  r >= -EPSILON &&
  r <= 1 + EPSILON &&
  g >= -EPSILON &&
  g <= 1 + EPSILON &&
  b >= -EPSILON &&
  b <= 1 + EPSILON;

/**
 * The nearest in-gamut sRGB colour at this lightness and hue, found by halving
 * the chroma. A saturated accent at step 100 or 900 has no sRGB answer at its
 * own chroma; lowering chroma keeps the hue and the lightness, which is what a
 * ramp step is for, where clipping the channels would move both.
 */
export function clampToGamut({ L, C, H }) {
  const lightness = clamp(L, 0, 1);
  const settle = (chroma) => {
    const rgb = oklchToRgb({ L: lightness, C: chroma, H });
    return { r: clamp(rgb.r, 0, 1), g: clamp(rgb.g, 0, 1), b: clamp(rgb.b, 0, 1), a: 1 };
  };
  const wanted = Math.max(C, 0);
  if (inGamut(oklchToRgb({ L: lightness, C: wanted, H }))) return settle(wanted);
  let low = 0;
  let high = wanted;
  for (let attempt = 0; attempt < 24; attempt += 1) {
    const middle = (low + high) / 2;
    if (inGamut(oklchToRgb({ L: lightness, C: middle, H }))) low = middle;
    else high = middle;
  }
  return settle(low);
}

/* ----------------------------------------------------------------- contrast */

/** WCAG 2.x relative luminance of an sRGB colour 0-1. */
export function luminance({ r, g, b }) {
  return 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b);
}

/**
 * WCAG contrast ratio between two colour strings, 1 to 21. A translucent
 * foreground is composited over the background first, because that is what the
 * eye sees and the gate is about what can be read.
 */
export function contrast(foreground, background) {
  const back = parse(background);
  let front = parse(foreground);
  if (!back || !front) return 0;
  if (front.a < 1) {
    front = {
      r: front.r * front.a + back.r * (1 - front.a),
      g: front.g * front.a + back.g * (1 - front.a),
      b: front.b * front.a + back.b * (1 - front.a),
      a: 1,
    };
  }
  const a = luminance(front);
  const b = luminance(back);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

/* --------------------------------------------------------------------- ramps */

/**
 * Nine steps from one base colour on the given lightness stops, in OKLCH.
 *
 * The base's hue is held for every step. Its chroma is held through the middle
 * of the ramp and eased towards the ends, because a step at L 0.97 or L 0.25
 * carrying the base's full chroma reads as a different colour rather than as
 * the same one lighter or darker. Every step is then clamped into sRGB.
 */
export function ramp(base, stops, { chromaEase = 0.55 } = {}) {
  const source = toOklch(base);
  if (!source) throw new Error(`ramp() needs a colour, got ${base}`);
  const middle = stops[Math.floor(stops.length / 2)];
  return stops.map((L) => {
    // Distance from the ramp's centre, 0 at the middle step and 1 at an end.
    const reach = Math.abs(L - middle) / Math.max(middle, 1 - middle);
    const C = source.C * (1 - chromaEase * reach ** 2);
    return toHex(clampToGamut({ L, C, H: source.H }));
  });
}

/** One step: the same maths as `ramp()` for a single lightness. */
export function step(base, L, options) {
  return ramp(base, [L], options)[0];
}

/**
 * `colour` at `percent` (0-1) over `over`. The soft tints are flattened rather
 * than left translucent so the contrast gate can measure them, and so a card
 * over a wallpaper does not let the scrim through a tint.
 */
export function mix(colour, over, percent) {
  const front = parse(colour);
  const back = parse(over);
  if (!front || !back) throw new Error(`mix() needs two colours, got ${colour} and ${over}`);
  const ratio = clamp(percent, 0, 1);
  return toHex({
    r: front.r * ratio + back.r * (1 - ratio),
    g: front.g * ratio + back.g * (1 - ratio),
    b: front.b * ratio + back.b * (1 - ratio),
    a: 1,
  });
}

/**
 * A translucent colour composited onto an opaque one, as the eye sees it. The
 * rail is a wash over `--bg` rather than a colour of its own, so the contrast
 * gate has to measure what it actually looks like.
 */
export function flatten(colour, over) {
  const front = parse(colour);
  const back = parse(over);
  if (!front || !back) throw new Error(`flatten() needs two colours, got ${colour} and ${over}`);
  if (front.a >= 1) return toHex({ ...front, a: 1 });
  return mix(toHex({ ...front, a: 1 }), toHex({ ...back, a: 1 }), front.a);
}

/** `colour` written as `rgba(r, g, b, opacity)` — a tint that must stay translucent. */
export function withAlpha(colour, opacity) {
  const rgb = parse(colour);
  if (!rgb) throw new Error(`withAlpha() needs a colour, got ${colour}`);
  const channel = (c) => Math.round(clamp(c, 0, 1) * 255);
  return `rgba(${channel(rgb.r)}, ${channel(rgb.g)}, ${channel(rgb.b)}, ${clamp(opacity, 0, 1)})`;
}

/**
 * The first of `steps` that clears `target` against `over`. How
 * `--accent-strong` is chosen: the raw violet clears 4.5 on white and not on a
 * tinted card, so the ramp is walked until something does rather than a darker
 * value being guessed.
 */
export function firstLegible(steps, over, target = 4.5) {
  for (const candidate of steps) if (contrast(candidate, over) >= target) return candidate;
  return steps[steps.length - 1];
}
