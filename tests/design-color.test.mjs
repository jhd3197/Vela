// The design system's colour maths, against values that are published rather
// than against whatever the implementation happens to produce.
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  clampToGamut,
  contrast,
  firstLegible,
  flatten,
  luminance,
  mix,
  parse,
  ramp,
  rgbToOklch,
  toHex,
  toOklch,
  withAlpha,
} from '../web/src/design/color.js';

const near = (actual, expected, tolerance, what) =>
  assert.ok(
    Math.abs(actual - expected) <= tolerance,
    `${what}: expected ${expected} ± ${tolerance}, got ${actual}`,
  );

test('parses every colour form the stylesheet writes', () => {
  assert.deepEqual(parse('#fff'), { r: 1, g: 1, b: 1, a: 1 });
  assert.deepEqual(parse('#000000'), { r: 0, g: 0, b: 0, a: 1 });
  assert.equal(parse('#7b4dff').r, 0x7b / 255);
  assert.equal(parse('#00000080').a, 0x80 / 255);
  assert.equal(parse('rgba(9, 15, 24, 0.85)').a, 0.85);
  assert.equal(parse('rgb(255 255 255)').g, 1);
  assert.equal(parse('color-mix(in srgb, red 10%, blue)'), null);
  assert.equal(parse('var(--accent)'), null);
});

test('round-trips a hex through OKLCH without moving it', () => {
  for (const hex of ['#ffffff', '#000000', '#7b4dff', '#2fd3f6', '#111a27', '#21a377']) {
    const { L, C, H } = toOklch(hex);
    assert.equal(toHex(clampToGamut({ L, C, H })), hex, `${hex} did not survive the round trip`);
  }
});

test('matches Ottosson’s published OKLab values', () => {
  // White is L 1 with no chroma; black is L 0. sRGB primaries are the values
  // the OKLab reference article prints for them.
  near(rgbToOklch(parse('#ffffff')).L, 1, 0.001, 'white L');
  near(rgbToOklch(parse('#ffffff')).C, 0, 0.001, 'white C');
  near(rgbToOklch(parse('#000000')).L, 0, 0.001, 'black L');
  near(rgbToOklch(parse('#ff0000')).L, 0.6279, 0.002, 'red L');
  near(rgbToOklch(parse('#ff0000')).H, 29.23, 0.5, 'red hue');
  near(rgbToOklch(parse('#00ff00')).L, 0.8664, 0.002, 'green L');
  near(rgbToOklch(parse('#0000ff')).L, 0.452, 0.002, 'blue L');
  // The Vela violet, the hue the whole accent ramp is built on.
  near(rgbToOklch(parse('#7b4dff')).H, 287.9, 0.5, 'Vela violet hue');
});

test('reports the WCAG ratios the token sheet claims', () => {
  near(contrast('#000000', '#ffffff'), 21, 0.01, 'black on white');
  near(contrast('#ffffff', '#ffffff'), 1, 0.001, 'white on white');
  // The comment in the generated sheet: the raw violet clears 4.5 on white.
  near(contrast('#7b4dff', '#ffffff'), 4.83, 0.02, 'Vela violet on white');
  near(contrast('#292b31', '#ffffff'), 14.15, 0.02, 'ink on white');
  // Contrast is symmetric.
  near(contrast('#7b4dff', '#ffffff'), contrast('#ffffff', '#7b4dff'), 0.001, 'symmetry');
  near(luminance(parse('#ffffff')), 1, 0.001, 'white luminance');
});

test('composites a translucent foreground before measuring it', () => {
  // A 14% white line on navy is not white: measuring it as white would pass a
  // border that nobody can see.
  const asWritten = contrast('rgba(232, 236, 244, 0.14)', '#1d2939');
  const asOpaque = contrast('#e8ecf4', '#1d2939');
  assert.ok(asWritten < asOpaque, 'the translucent border measured as if it were opaque');
  near(
    asWritten,
    contrast(flatten('rgba(232, 236, 244, 0.14)', '#1d2939'), '#1d2939'),
    0.01,
    'flatten and composite disagree',
  );
});

test('a ramp is nine in-gamut steps that get darker, keeping the hue', () => {
  const stops = [0.971, 0.93, 0.869, 0.779, 0.68, 0.579, 0.48, 0.38, 0.29];
  for (const base of ['#7b4dff', '#9397ab', '#2fd3f6', '#d5483a', '#ff0000']) {
    const steps = ramp(base, stops);
    assert.equal(steps.length, 9);
    const hue = toOklch(base).H;
    let previous = Infinity;
    for (const [index, hex] of steps.entries()) {
      assert.match(hex, /^#[0-9a-f]{6}$/, `${base} step ${index} is not an sRGB hex`);
      const measured = toOklch(hex);
      near(measured.L, stops[index], 0.006, `${base} step ${index} lightness`);
      // Hue is asserted as the distance it actually moves the colour, not as
      // an angle: at the lightest step the gamut clamp leaves so little chroma
      // that 8-bit rounding swings the measured angle by degrees without
      // moving anything the eye could find. The chord in a*b* does not lie.
      const swing = Math.abs(((measured.H - hue + 540) % 360) - 180);
      const drift = 2 * measured.C * Math.sin((swing * Math.PI) / 360);
      assert.ok(drift < 0.004, `${base} step ${index} left the hue: ${drift.toFixed(4)} in a*b*`);
      assert.ok(measured.L < previous, `${base} step ${index} is not darker than the one before`);
      previous = measured.L;
    }
  }
});

test('clamps a saturated accent into sRGB instead of clipping it', () => {
  // Nothing at L 0.29 can carry the violet's chroma. The step has to stay the
  // violet's hue and the ramp's lightness; only the chroma may give.
  const dark = clampToGamut({ L: 0.29, C: 0.2469, H: 287.9 });
  for (const channel of ['r', 'g', 'b']) {
    assert.ok(dark[channel] >= 0 && dark[channel] <= 1, `channel ${channel} left sRGB`);
  }
  near(toOklch(toHex(dark)).L, 0.29, 0.006, 'clamped lightness');
  near(toOklch(toHex(dark)).H, 287.9, 2.5, 'clamped hue');
});

test('mixes, tints and picks the first legible step', () => {
  assert.equal(mix('#000000', '#ffffff', 0.5), '#808080');
  assert.equal(mix('#7b4dff', '#ffffff', 0), '#ffffff');
  assert.equal(withAlpha('#7b4dff', 0.12), 'rgba(123, 77, 255, 0.12)');
  const steps = ramp('#7b4dff', [0.971, 0.93, 0.869, 0.779, 0.68, 0.579, 0.48, 0.38, 0.29]);
  const picked = firstLegible(steps, '#ffffff', 4.5);
  assert.ok(contrast(picked, '#ffffff') >= 4.5, 'firstLegible returned an illegible step');
  // It is the *first* one that clears, not simply the darkest.
  const earlier = steps[steps.indexOf(picked) - 1];
  assert.ok(contrast(earlier, '#ffffff') < 4.5, 'firstLegible skipped a step that already cleared');
});
