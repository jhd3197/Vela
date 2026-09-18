// The `ds/` components: that each renders its primitive's class, that the ones
// carrying a measurement say so to a screen reader, and that a component handed
// nothing draws nothing rather than an empty chart.
//
// Rendered to a string with React's server renderer: no DOM, no browser, no
// test framework beyond node:test. Node cannot import `.jsx`, so the layer is
// bundled first with the esbuild Vite already ships -- which also means the
// test exercises the same transform the dashboard is built with, rather than a
// second one that could disagree about what the components compile to.
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const web = new URL("../web/", import.meta.url);
const esbuild = require("../web/node_modules/esbuild");
const local = (relative) => fileURLToPath(new URL(relative, web));

// The entry lives outside the repository, so esbuild is told where the
// dashboard's own modules are rather than guessing from the file's location.
const scratch = mkdtempSync(path.join(tmpdir(), "vela-ds-"));
const entry = path.join(scratch, "entry.jsx");
writeFileSync(
  entry,
  [
    `export * from ${JSON.stringify(local("src/components/ds/index.js"))};`,
    `export { createElement } from 'react';`,
    `export { renderToStaticMarkup } from 'react-dom/server';`,
  ].join("\n"),
);

const bundle = path.join(scratch, "bundle.mjs");
await esbuild.build({
  entryPoints: [entry],
  bundle: true,
  format: "esm",
  jsx: "automatic",
  outfile: bundle,
  nodePaths: [local("node_modules")],
  logLevel: "silent",
});

const bundled = await import(pathToFileURL(bundle).href);
const { createElement, renderToStaticMarkup } = bundled;

// Each component is wrapped so calling it builds an element rather than running
// its body: `useId` and the rest are hooks, and a hook called outside a render
// has no React to talk to.
const wrap =
  (Component) =>
  (props = {}) =>
    createElement(Component, props);
const Bars = wrap(bundled.Bars);
const Card = wrap(bundled.Card);
const KeyValue = wrap(bundled.KeyValue);
const Meter = wrap(bundled.Meter);
const Pill = wrap(bundled.Pill);
const Ring = wrap(bundled.Ring);
const Rows = wrap(bundled.Rows);
const SegControl = wrap(bundled.SegControl);
const Sparkline = wrap(bundled.Sparkline);
const Stat = wrap(bundled.Stat);
const Tag = wrap(bundled.Tag);

const render = (element) => renderToStaticMarkup(element);

test("a card draws the prototype recipe, and only the parts it was given", () => {
  const full = render(
    Card({
      icon: "i",
      title: "Health",
      meta: "2m",
      tone: "green",
      children: "body",
    }),
  );
  assert.match(full, /class="vela-card"/);
  assert.match(full, /class="vela-card-icon" data-tone="green"/);
  assert.match(full, /<h3 class="vela-card-title">Health<\/h3>/);
  assert.match(full, /class="vela-card-meta">2m</);
  assert.match(full, /class="vela-card-body">body</);

  // A card with nothing in its header draws no header at all, rather than an
  // empty row that pushes the body down by its own height.
  const bare = render(Card({ children: "body" }));
  assert.doesNotMatch(bare, /vela-card-head/);
  assert.doesNotMatch(bare, /vela-card-foot/);

  // On the desk the frame has already painted the surface.
  assert.match(render(Card({ plain: true, children: "x" })), /data-plain=""/);
});

test("a stat shows an em dash rather than nothing when it has no number", () => {
  assert.match(render(Stat({ value: 42, unit: "GB" })), /vela-stat-unit">GB</);
  for (const nothing of [null, undefined, ""]) {
    const markup = render(Stat({ value: nothing, unit: "GB" }));
    assert.match(markup, /—/, `a stat with ${nothing} did not fall back`);
    // And no stray unit floating beside the dash.
    assert.doesNotMatch(markup, /vela-stat-unit/);
  }
});

test("a delta is tinted by the widget, not by its sign", () => {
  // "Storage used, up 4 %" is not good news, so a rise is not green by default.
  assert.match(
    render(Stat({ value: 1, delta: "+4%" })),
    /class="vela-stat-delta"/,
  );
  assert.match(
    render(Stat({ value: 1, delta: "+4%", deltaTone: "red" })),
    /data-tone="red"/,
  );
});

test("a meter is a progressbar and clamps what it is given", () => {
  const markup = render(
    Meter({ percent: 62, label: "Memory", detail: "6 / 10 GB" }),
  );
  assert.match(markup, /role="progressbar"/);
  assert.match(markup, /aria-valuenow="62"/);
  assert.match(markup, /aria-label="Memory"/);
  assert.match(markup, /width:62%/);

  // A malformed summary must not run the fill off its track.
  assert.match(render(Meter({ percent: 240 })), /width:100%/);
  assert.match(render(Meter({ percent: -7 })), /width:0%/);
  assert.match(render(Meter({ percent: "nonsense" })), /width:0%/);
  // With nothing to label it, the bar still says what it is.
  assert.match(render(Meter({ percent: 10 })), /aria-label="Progress"/);
});

test("a ring reads as an image with its proportion spoken", () => {
  const markup = render(Ring({ percent: 92, caption: "checks" }));
  assert.match(markup, /role="img"/);
  assert.match(markup, /aria-label="92 per cent"/);
  assert.match(markup, /class="vela-ring-value">92%</);
  assert.match(markup, /class="vela-ring-caption">checks</);
  // A full ring leaves no gap, an empty one is all gap.
  assert.match(render(Ring({ percent: 100 })), /stroke-dashoffset="0"/);
  assert.match(
    render(Ring({ percent: 0, size: 76, thickness: 7 })),
    /stroke-dashoffset="216/,
  );
  // A label of its own wins over the generated one.
  assert.match(
    render(Ring({ percent: 50, label: "Eleven of twelve" })),
    /aria-label="Eleven of/,
  );
});

test("bars weight the last one and anything above the median", () => {
  // Sorted, the series is [1, 2, 2, 3, 9], so the median is 2 and the bars
  // above it are the 3 and the 9. The final bar is the last one whatever its
  // value -- it is today, and today is what the eye should find first.
  const markup = render(
    Bars({ series: [1, 2, 3, 9, 2], caption: "this week" }),
  );
  const weights = [...markup.matchAll(/data-weight="(\w+)"/g)].map(
    (match) => match[1],
  );
  assert.deepEqual(weights, ["high", "high", "last"]);
  assert.match(markup, /class="vela-bars-caption">this week</);

  // A run of zeroes is still a row of marks; a chart of nothing would read as a
  // measurement of zero that was never taken.
  assert.match(render(Bars({ series: [0, 0, 0] })), /height:4%/);
  // Nothing to draw draws nothing.
  assert.equal(render(Bars({ series: [] })), "");
  assert.equal(render(Bars({ series: ["x", null, NaN] })), "");
});

test("a fixed domain beats scaling to the data", () => {
  // The same series, read as a percentage and read on its own terms.
  const scaled = render(Bars({ series: [10, 20] }));
  const fixed = render(Bars({ series: [10, 20], domain: [0, 100] }));
  assert.match(scaled, /height:100%/);
  assert.match(fixed, /height:20%/);
  assert.doesNotMatch(fixed, /height:100%/);
});

test("a key/value list and a row list stop at eight", () => {
  const many = Array.from({ length: 14 }, (_, index) => ({
    label: `row ${index}`,
    value: index,
    detail: "d",
  }));
  assert.equal(
    render(KeyValue({ rows: many })).match(/vela-kv-row/g).length,
    8,
  );
  assert.equal(
    render(Rows({ rows: many })).match(/class="vela-row"/g).length,
    8,
  );
  assert.equal(render(KeyValue({ rows: [] })), "");
  assert.equal(render(Rows({ rows: [] })), "");
});

test("a key/value row tints its value and not the whole row", () => {
  const markup = render(
    KeyValue({ rows: [{ label: "Failures", value: "2", tone: "red" }] }),
  );
  assert.match(markup, /<dd class="vela-kv-value" data-tone="red">2<\/dd>/);
  assert.doesNotMatch(markup, /<dt[^>]*data-tone/);
});

test("a row leads with its own dot unless it brings something else", () => {
  assert.match(
    render(Rows({ rows: [{ label: "a", tone: "cyan" }] })),
    /vela-row-dot" data-tone="cyan"/,
  );
  assert.doesNotMatch(
    render(Rows({ rows: [{ label: "a", lead: "X" }] })),
    /vela-row-dot/,
  );
  assert.match(
    render(Rows({ rows: [{ label: "a", tail: "2m" }] })),
    /vela-row-tail">2m</,
  );
});

test("a pill and a tag carry a tone as data, not as a colour", () => {
  assert.match(
    render(Pill({ tone: "cyan", children: "Running" })),
    /data-tone="cyan"/,
  );
  assert.match(
    render(Pill({ tone: "cyan", children: "Running" })),
    /vela-pill-dot/,
  );
  assert.doesNotMatch(
    render(Pill({ dot: false, children: "x" })),
    /vela-pill-dot/,
  );
  assert.match(render(Tag({ children: "wellness" })), /class="vela-tag"/);
  // Neither of them ever writes a colour into the markup.
  assert.doesNotMatch(
    render(Pill({ tone: "red", children: "x" })),
    /#[0-9a-f]{3}|rgb\(/i,
  );
});

test("a segmented control is a radio group, so the keyboard already works", () => {
  const markup = render(
    SegControl({
      label: "Base",
      value: "dark",
      options: [
        { value: "light", label: "Light" },
        { value: "dark", label: "Dark" },
      ],
    }),
  );
  assert.match(markup, /role="radiogroup"/);
  assert.match(markup, /aria-label="Base"/);
  assert.equal(markup.match(/type="radio"/g).length, 2);
  assert.equal(markup.match(/checked=""/g).length, 1);
  // The group name is per-instance, so two controls on one screen cannot
  // capture each other's selection. They have to be measured in one tree:
  // `useId` promises uniqueness within a render, and two separate renders each
  // start their counter again.
  const pair = render(
    createElement(
      "div",
      null,
      SegControl({ options: [{ value: "a", label: "A" }] }),
      SegControl({ options: [{ value: "b", label: "B" }] }),
    ),
  );
  const names = [...pair.matchAll(/name="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(
    new Set(names).size,
    2,
    "two segmented controls share a group name",
  );
});

test("a sparkline needs two points, and its gradient id is its own", () => {
  assert.equal(render(Sparkline({ series: [4] })), "");
  const markup = render(Sparkline({ series: [1, 4, 2, 8] }));
  assert.match(markup, /class="vela-sparkline"/);
  assert.match(markup, /aria-hidden="true"/);
  // Two on one board must not capture each other's fill, so they are measured
  // in one tree -- `useId` starts its counter again for each separate render.
  const pair = render(
    createElement(
      "div",
      null,
      Sparkline({ series: [1, 2] }),
      Sparkline({ series: [3, 4] }),
    ),
  );
  const ids = [...pair.matchAll(/id="(vela-spark-[^"]+)"/g)].map(
    (match) => match[1],
  );
  assert.equal(ids.length, 2);
  assert.equal(new Set(ids).size, 2, "two sparklines share a gradient id");
  // Given a label it becomes an image with a name instead of decoration.
  assert.match(
    render(Sparkline({ series: [1, 2], label: "CPU" })),
    /role="img"/,
  );
});

test("every primitive takes its colour from a token, never from the markup", () => {
  const rendered = [
    Card({ tone: "red", icon: "i", title: "t", children: "x" }),
    Stat({ value: 1, delta: "-1", deltaTone: "green" }),
    Meter({ percent: 50, tone: "amber" }),
    Ring({ percent: 50, tone: "cyan" }),
    Bars({ series: [1, 2, 3] }),
    KeyValue({ rows: [{ label: "a", value: "b", tone: "red" }] }),
    Rows({ rows: [{ label: "a", tone: "green" }] }),
    Pill({ tone: "amber", children: "x" }),
    Tag({ tone: "cyan", children: "x" }),
    Sparkline({ series: [1, 2], tone: "green" }),
  ].map(render);
  for (const markup of rendered) {
    assert.doesNotMatch(
      markup,
      /#[0-9a-fA-F]{3,8}\b/,
      `a colour literal reached the markup`,
    );
    assert.doesNotMatch(markup, /rgba?\(/, `an rgb() reached the markup`);
  }
});
