// The surface renderer: that the contract's refusal rules hold, that the
// nodes draw with the design system's pieces, and that values are formatted
// for the viewer rather than trusted from the producer.
//
// Same harness as `design-primitives.test.mjs`: bundled with the esbuild
// Vite ships and rendered to a string, so the test exercises the same
// transform the dashboard is built with.
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

const scratch = mkdtempSync(path.join(tmpdir(), "vela-surface-"));
const entry = path.join(scratch, "entry.jsx");
writeFileSync(
  entry,
  [
    `export { default as SurfaceView } from ${JSON.stringify(local("src/components/SurfaceView.jsx"))};`,
    `export * from ${JSON.stringify(local("src/components/surface-format.js"))};`,
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
const { createElement, renderToStaticMarkup, formatPercent, formatSurfaceTime, formatSurfaceValue } =
  bundled;

const render = (surface, props = {}) =>
  renderToStaticMarkup(createElement(bundled.SurfaceView, { surface, ...props }));

test("a version this host does not know is refused whole, never drawn in part", () => {
  for (const surface of [null, { surface: 2, root: { type: "text", value: "secret" } }, {}]) {
    const markup = render(surface);
    assert.match(markup, /format this version cannot draw/);
    assert.doesNotMatch(markup, /secret/);
  }
});

test("an unknown node type degrades in place and the rest still draws", () => {
  const markup = render({
    surface: 1,
    root: {
      type: "stack",
      children: [
        { type: "text", value: "before" },
        { type: "hologram", value: "future" },
        { type: "text", value: "after" },
      ],
    },
  });
  assert.match(markup, /before/);
  assert.match(markup, /Cannot show this part\./);
  assert.match(markup, /after/);
});

test("an image is inline png, jpeg or webp data or it is nothing", () => {
  const good = render({
    surface: 1,
    root: { type: "image", src: "data:image/png;base64,aGVsbG8=", alt: "shot" },
  });
  assert.match(good, /<img/);
  assert.match(good, /alt="shot"/);
  for (const src of [
    "https://evil.example/x.png",
    "data:image/svg+xml;base64,PHN2Zz4=",
    "data:text/html;base64,PGI+",
    "javascript:alert(1)",
  ]) {
    const markup = render({ surface: 1, root: { type: "image", src, alt: "x" } });
    assert.doesNotMatch(markup, /<img/, src);
  }
});

test("a button needs a declared action and a wired host, or it renders nothing", () => {
  const doc = (action) => ({
    surface: 1,
    actions: [{ id: "restart", title: "Restart service" }],
    root: { type: "button", label: "Restart", action },
  });
  // An action the document did not declare is refused, per the contract.
  assert.doesNotMatch(render(doc("rm-everything"), { onAction() {} }), /<button/);
  // Declared, but this host is read-only: no onAction, no button.
  assert.doesNotMatch(render(doc("restart")), /<button/);
  // Declared and wired: the button draws.
  assert.match(render(doc("restart"), { onAction() {} }), />Restart</);
});

test("a span is clamped to the grid the host draws", () => {
  const markup = render({
    surface: 1,
    root: {
      type: "grid",
      columns: 6,
      children: [{ type: "text", value: "wide", span: 9 }],
    },
  });
  assert.match(markup, /grid-column:span 6/);
});

test("a desktop draws windows and a dock; a missing window makes an inert item", () => {
  const markup = render({
    surface: 1,
    root: {
      type: "desktop",
      title: "web-01",
      wallpaper: "dusk",
      windows: [
        { type: "window", id: "services", title: "Services", children: [{ type: "empty", message: "none" }] },
        { type: "window", id: "logs", title: "Logs", minimized: true, children: [] },
      ],
      dock: [
        { label: "services", window: "services" },
        { label: "logs", window: "logs" },
        { label: "ghost", window: "not-a-window" },
      ],
    },
  });
  assert.match(markup, /vela-synth--dusk/);
  assert.match(markup, /web-01/);
  assert.match(markup, /Services/);
  assert.doesNotMatch(markup, />Logs</, "a minimized window is not drawn");
  const dock = markup.slice(markup.indexOf("vela-synth-taskbar"));
  assert.match(dock, /<button[^>]*class="vela-synth-task[^"]*"[^>]*>[\s\S]*?services/);
  assert.match(dock, /is-minimized[^"]*"[^>]*>[\s\S]*?logs/, "the dock remembers it is away");
  // The item naming a window that is not there is drawn, linked to nothing.
  assert.match(dock, /<span class="vela-synth-task">[\s\S]*?ghost/);
});

test("values are formatted by the host, following the node's format", () => {
  const markup = render({
    surface: 1,
    root: {
      type: "stack",
      children: [
        { type: "stat", label: "CPU", value: 9.94, format: "percent" },
        { type: "stat", label: "RAM", value: 42.4, format: "percent" },
        { type: "stat", label: "Disk", value: 2048, format: "bytes" },
        {
          type: "keyvalue",
          rows: [
            { label: "requests", value: 1234567, format: "number" },
            { label: "up", value: 7261, format: "duration" },
          ],
        },
        { type: "table", columns: [{ key: "n", label: "N", format: "percent" }], rows: [{ n: 4.26 }] },
      ],
    },
  });
  assert.match(markup, /9\.9%/, "one decimal under ten");
  assert.match(markup, /42%/, "whole above");
  assert.match(markup, /2 KB/);
  assert.match(markup, /1,234,567/);
  assert.match(markup, /2 hours/);
  assert.match(markup, /4\.3%/);
});

test("text is plain: nothing is parsed as markup", () => {
  const markup = render({
    surface: 1,
    root: { type: "text", value: "<b>not bold</b>", style: "mono" },
  });
  assert.doesNotMatch(markup, /<b>not bold<\/b>/);
  assert.match(markup, /&lt;b&gt;not bold&lt;\/b&gt;/);
  assert.match(markup, /vela-surface-text--mono/);
});

test("the formatting helpers stand on their own", () => {
  assert.equal(formatPercent(9.94), "9.9%");
  assert.equal(formatPercent(10), "10%");
  assert.equal(formatSurfaceValue(null, "number"), "—");
  assert.equal(formatSurfaceValue("plain words", "number"), "plain words");
  assert.equal(formatSurfaceValue(0, "bytes"), "0 B");
  assert.equal(formatSurfaceValue(59, "duration"), "less than a minute");
  const stamp = formatSurfaceTime("2026-02-03T10:20:00Z");
  assert.ok(stamp.length > 4 && stamp !== "—", `a real date formats: ${stamp}`);
  assert.equal(formatSurfaceTime("not a date"), "—");
});
