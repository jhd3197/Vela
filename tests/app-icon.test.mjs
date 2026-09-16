import test from "node:test";
import assert from "node:assert/strict";
import {
  artworkKey,
  monogram,
  ID_MARKS,
  CATEGORY_MARKS,
} from "../web/src/appArtworkKey.js";
import {
  parseColor,
  toOklch,
  tileTones,
  tileColor,
} from "../web/src/appTint.js";

test("an app with its own artwork keeps it, whatever its category says", () => {
  for (const id of ID_MARKS) {
    assert.deepEqual(artworkKey({ id, category: "productivity" }), {
      kind: "id",
      key: id,
    });
  }
  // The id wins over a category that would otherwise resolve elsewhere.
  assert.deepEqual(
    artworkKey({ id: "health", category: "connected", name: "Health" }),
    {
      kind: "id",
      key: "health",
    },
  );
});

test("categories resolve however the manifest capitalises them", () => {
  for (const category of CATEGORY_MARKS) {
    assert.deepEqual(artworkKey({ id: "x", category }), {
      kind: "category",
      key: category,
    });
    assert.deepEqual(
      artworkKey({ id: "x", category: category.toUpperCase() }),
      {
        kind: "category",
        key: category,
      },
    );
  }
  // The case that used to fall through to the generic grid.
  assert.deepEqual(artworkKey({ id: "some-tool", category: "Developer" }), {
    kind: "category",
    key: "developer",
  });
});

test("a connected service is identified by its initial, by prefix or category", () => {
  assert.deepEqual(artworkKey({ id: "web--1", name: "GitHub" }), {
    kind: "monogram",
    key: "G",
  });
  assert.deepEqual(
    artworkKey({ id: "anything", category: "connected", name: "jellyfin" }),
    {
      kind: "monogram",
      key: "J",
    },
  );
  // Punctuation and whitespace are skipped; digits count; accents are kept.
  assert.equal(monogram("  ·  home assistant"), "H");
  assert.equal(monogram("1Password"), "1");
  assert.equal(monogram("élan"), "É");
  // A service with no usable name falls back rather than showing an empty tile.
  assert.deepEqual(artworkKey({ id: "web--2", name: "···" }), {
    kind: "unknown",
  });
  assert.deepEqual(artworkKey({ id: "web--3" }), { kind: "unknown" });
});

test("anything unrecognised gets the generic mark", () => {
  for (const app of [
    null,
    undefined,
    {},
    { id: "nope", category: "mystery" },
  ]) {
    assert.deepEqual(artworkKey(app), { kind: "unknown" });
  }
});

test("a declared colour is kept; an app without one gets a stable hue", () => {
  assert.equal(tileColor({ id: "notes", color: "#7c4dee" }), "#7c4dee");

  // Same id, same hue on every render; different ids do not all collapse to one.
  assert.equal(tileColor({ id: "alpha" }), tileColor({ id: "alpha" }));
  const ids = [
    "alpha",
    "beta",
    "gamma",
    "delta",
    "epsilon",
    "zeta",
    "eta",
    "theta",
  ];
  assert.ok(new Set(ids.map((id) => tileColor({ id }))).size > 1);

  // Every derived hue must still be usable as a tile.
  for (const id of [...ids, "", undefined]) {
    assert.ok(tileTones(tileColor({ id })), `${id} should produce tones`);
  }
});

test("colours are read from hex and rgb, and refused otherwise", () => {
  assert.deepEqual(parseColor("#7c4dee"), parseColor("rgb(124, 77, 238)"));
  assert.deepEqual(parseColor("#abc"), parseColor("#aabbcc"));
  assert.deepEqual(parseColor("#7C4DEE"), parseColor("#7c4dee"));
  for (const bad of [
    "",
    null,
    "rebeccapurple",
    "#12345",
    "rgb(1,2)",
    "nonsense",
  ]) {
    assert.equal(parseColor(bad), null);
    assert.equal(tileTones(bad), null, `${bad} should have no tones`);
  }
});

test("tile tones normalise lightness so every app carries the same weight", () => {
  // These are the colours the first-party apps actually declare. They differ
  // in lightness by a lot, which is what used to make some tiles wash out.
  const declared = [
    "#7c4dee",
    "#9184d9",
    "#2bb6d8",
    "#21a377",
    "#75798c",
    "#8b5cf6",
    "#a59c8b",
  ];
  const marks = declared.map(
    (color) => toOklch(parseColor(tileTones(color).mark)).L,
  );
  const washes = declared.map(
    (color) => toOklch(parseColor(tileTones(color).wash)).L,
  );
  const spread = (values) => Math.max(...values) - Math.min(...values);

  assert.ok(
    spread(declared.map((c) => toOklch(parseColor(c)).L)) > 0.1,
    "inputs vary in lightness",
  );
  assert.ok(
    spread(marks) < 0.03,
    `marks should land together, spread was ${spread(marks)}`,
  );
  assert.ok(
    spread(washes) < 0.03,
    `washes should land together, spread was ${spread(washes)}`,
  );
  // The mark must stay darker than its ground in light mode, and lighter in dark mode.
  for (const color of declared) {
    const tones = tileTones(color);
    assert.ok(
      toOklch(parseColor(tones.mark)).L < toOklch(parseColor(tones.wash)).L,
    );
    assert.ok(
      toOklch(parseColor(tones.markDark)).L >
        toOklch(parseColor(tones.washDark)).L,
    );
  }
});

test("tones stay inside sRGB instead of clipping to neon", () => {
  // Pale cyan and green at the wash lightness do not exist in sRGB. Chroma has
  // to come down; clipping the channels instead is what made them glow.
  for (const color of ["#2bb6d8", "#21a377", "#00ff00", "#ff0000"]) {
    for (const tone of Object.values(tileTones(color))) {
      const rgb = parseColor(tone);
      assert.ok(rgb, `${tone} should parse`);
      assert.ok(
        rgb.every((channel) => channel >= 0 && channel <= 1),
        `${color} produced an out-of-range tone ${tone}`,
      );
    }
  }
  // A grey app stays grey: chroma is capped, never invented.
  const grey = tileTones("#75798c");
  assert.ok(
    toOklch(parseColor(grey.mark)).C < 0.04,
    "a grey app must not become colourful",
  );
});

// The brand palette, checked as numbers rather than trusted as hex. An app that
// declares no colour of its own is drawn in Vela's violet, so that default has
// to clear the same contrast bar every other tile does — in both themes.
const DEFAULT_APP_COLOR = "#7b4dff";

function relativeLuminance(color) {
  const channel = (value) =>
    value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  const [r, g, b] = parseColor(color).map(channel);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a, b) {
  const [high, low] = [relativeLuminance(a), relativeLuminance(b)].sort(
    (x, y) => y - x,
  );
  return (high + 0.05) / (low + 0.05);
}

test("the default app tint stays readable on its own tile in both themes", () => {
  const tones = tileTones(DEFAULT_APP_COLOR);
  assert.ok(tones, "the default colour must produce tones");
  // 4.5:1 is the bar for text; a tile's mark is held to it because the mark is
  // often a letter rather than a glyph.
  assert.ok(
    contrast(tones.mark, tones.wash) >= 4.5,
    `light tile contrast was ${contrast(tones.mark, tones.wash).toFixed(2)}:1`,
  );
  assert.ok(
    contrast(tones.markDark, tones.washDark) >= 4.5,
    `dark tile contrast was ${contrast(tones.markDark, tones.washDark).toFixed(2)}:1`,
  );
});

test("the accent Vela writes text in clears 4.5:1 on the surface behind it", () => {
  // These mirror `_tokens.scss`. The raw violet clears the bar on white, which
  // is why it can be `--accent`; `--accent-strong` is what tinted grounds use.
  assert.ok(contrast("#7b4dff", "#ffffff") >= 4.5, "light --accent on a card");
  assert.ok(
    contrast("#5b2edb", "#efe6ff") >= 4.5,
    "light --accent-strong on --accent-soft",
  );
  assert.ok(contrast("#a78bff", "#1d2939") >= 4.5, "dark --accent on a card");
  assert.ok(
    contrast("#c9b0ff", "#1d2939") >= 4.5,
    "dark --accent-strong on a card",
  );
  // The running dot is a graphic, not text, so it is held to 3:1 instead.
  assert.ok(contrast("#1595b6", "#ffffff") >= 3, "light --cyan on a card");
  assert.ok(contrast("#2fd3f6", "#1d2939") >= 3, "dark --cyan on a card");
});
