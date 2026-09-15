// Tile colours derived from an app's declared colour.
//
// Apps choose their own colour, and those choices differ in lightness as much
// as in hue: a pale violet and a deep violet used to produce tiles with very
// different contrast even though both apps are "purple". Normalising lightness
// in OKLab gives every tile the same weight while leaving hue — the part that
// identifies the app — untouched. Chroma is only capped, never raised, so a
// deliberately grey app stays grey instead of being invented into a colour.

const LIGHT = { mark: 0.46, wash: 0.955, line: 0.86 };
const DARK = { mark: 0.82, wash: 0.315, line: 0.42 };
const MAX_CHROMA = 0.17;
const WASH_CHROMA = 0.42; // share of the hue's chroma kept in the ground
const LINE_CHROMA = 0.62;

function srgbToLinear(value) {
  return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
}

function linearToSrgb(value) {
  return value <= 0.0031308 ? value * 12.92 : 1.055 * value ** (1 / 2.4) - 0.055;
}

// Accepts #rgb, #rrggbb and rgb()/rgba() so a colour that reached the browser
// as a computed value still resolves. Anything else returns null and the
// caller falls back.
export function parseColor(input) {
  const text = String(input || '').trim();
  const hex = text.replace(/^#/, '');
  if (/^[0-9a-f]{3}$/i.test(hex)) {
    return [0, 1, 2].map((i) => parseInt(hex[i] + hex[i], 16) / 255);
  }
  if (/^[0-9a-f]{6}$/i.test(hex)) {
    return [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  }
  const rgb = text.match(/^rgba?\(([^)]+)\)$/i);
  if (rgb) {
    const parts = rgb[1]
      .split(/[\s,/]+/)
      .filter(Boolean)
      .slice(0, 3);
    if (parts.length === 3) {
      const values = parts.map((part) =>
        part.endsWith('%') ? parseFloat(part) / 100 : parseFloat(part) / 255,
      );
      if (values.every((value) => Number.isFinite(value))) return values;
    }
  }
  return null;
}

export function toOklch([r, g, b]) {
  const lr = srgbToLinear(r);
  const lg = srgbToLinear(g);
  const lb = srgbToLinear(b);
  const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
  const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
  const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
  const L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
  const A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  return { L, C: Math.hypot(A, B), h: Math.atan2(B, A) };
}

function toLinearRgb({ L, C, h }) {
  const A = C * Math.cos(h);
  const B = C * Math.sin(h);
  const l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3;
  const m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3;
  const s = (L - 0.0894841775 * A - 1.291485548 * B) ** 3;
  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
}

const EPSILON = 1e-4;

function inGamut(rgb) {
  return rgb.every((value) => value >= -EPSILON && value <= 1 + EPSILON);
}

// A pale cyan at the lightness we want does not exist in sRGB, and clipping
// the channels turns it neon. Reduce chroma until the colour fits instead, so
// every wash stays as quiet as it was asked to be.
export function fromOklch({ L, C, h }) {
  let rgb = toLinearRgb({ L, C, h });
  if (!inGamut(rgb)) {
    let low = 0;
    let high = C;
    for (let i = 0; i < 18; i += 1) {
      const mid = (low + high) / 2;
      if (inGamut(toLinearRgb({ L, C: mid, h }))) low = mid;
      else high = mid;
    }
    rgb = toLinearRgb({ L, C: low, h });
  }
  return `#${rgb
    .map((value) => {
      const byte = Math.round(Math.min(1, Math.max(0, linearToSrgb(value))) * 255);
      return byte.toString(16).padStart(2, '0');
    })
    .join('')}`;
}

// An app that declares no colour of its own used to share a single violet
// with every other undeclared app, which made them indistinguishable. Derive a
// stable hue from the id instead. An app that does declare a colour keeps it.
const FALLBACK_HUES = [
  '#7c4dee',
  '#2bb6d8',
  '#21a377',
  '#d08a2e',
  '#d5483a',
  '#4d7ceb',
  '#b4569e',
  '#5f9e35',
];

export function fallbackHue(id) {
  const key = String(id || '');
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  return FALLBACK_HUES[hash % FALLBACK_HUES.length];
}

export function tileColor(app) {
  return app?.color || fallbackHue(app?.id);
}

// The three values a tile needs: the mark, the ground it sits on and the
// hairline that separates the tile from the page. Returned for both themes so
// the stylesheet can switch without recomputing.
export function tileTones(color) {
  const rgb = parseColor(color);
  if (!rgb) return null;
  const { C, h } = toOklch(rgb);
  const chroma = Math.min(C, MAX_CHROMA);
  const tone = (L, scale) => fromOklch({ L, C: chroma * scale, h });
  return {
    mark: tone(LIGHT.mark, 1),
    wash: tone(LIGHT.wash, WASH_CHROMA),
    line: tone(LIGHT.line, LINE_CHROMA),
    markDark: tone(DARK.mark, 1),
    washDark: tone(DARK.wash, WASH_CHROMA),
    lineDark: tone(DARK.line, LINE_CHROMA),
  };
}
