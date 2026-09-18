# The Vela design system

How Vela is coloured, spaced and drawn, and what a contributor has to know to
add something that looks like the rest of it.

Everything described here is built. Where a promise is kept by a check rather
than by care, this says which check.

## The short version

- **Colour is data.** `web/src/design/theme.vela.json` holds the stock theme.
  `web/scripts/build-tokens.mjs` turns it into `web/src/styles/_tokens.scss`.
  The stylesheet is generated and committed; editing it by hand fails the check.
- **No colour is written anywhere else.** A hex outside the token sheet fails
  the check in the file it was written in.
- **Rhythm is a scale.** `padding`, `gap` and `margin` read `--space-1…8`.
- **Widgets are drawn with primitives.** `web/src/components/ds/` over
  `web/src/styles/primitives/`. A widget an app published and one Vela drew
  should be indistinguishable in chrome.

## Tokens

### What a theme may set

Thirty-three canonical tokens, in groups. These are the only names a theme
document may carry.

| Group | Tokens | Value |
| --- | --- | --- |
| ground | `--bg`, `--bg-rail`, `--bg-workspace`, `--bg-panel`, `--bg-header`, `--bg-card`, `--bg-field`, `--bg-inset`, `--bg-pop` | a colour |
| text | `--text`, `--text-dim`, `--text-faint` | a colour |
| border | `--border`, `--border-strong` | a colour |
| role | `--neutral`, `--accent`, `--cyan`, `--green`, `--amber`, `--red` | a colour |
| radius | `--radius-sm`, `--radius-md`, `--radius-lg` | a length |
| font | `--font`, `--mono` | a stack from the allow-list |
| shadow | `--shadow-sm`, `--shadow-md`, `--shadow-lg` | a shadow |
| glow | `--glow` | a gradient |
| wall | `--scrim`, `--glass` | a colour |
| ink | `--on-wall`, `--on-accent` | a colour |

`--scrim` is the ink a wallpaper is veiled with and a dialog is backed with.
`--glass` is the surface that floats on a wallpaper. `--on-wall` is ink drawn
straight onto a photograph, where `--text` would vanish. `--on-accent` is ink on
a button flooded with the accent — a theme with a pale accent needs a dark one.

### What is derived, and never set

- **Ramps.** `--neutral-100…900`, `--accent-100…900`, `--cyan-100…900`, derived
  in OKLCH from the role's single base colour on nine shared lightness stops:
  `0.971, 0.930, 0.869, 0.779, 0.680, 0.579, 0.480, 0.380, 0.290`. Because the
  stops are shared, step 600 of the accent and step 600 of the neutral carry the
  same visual weight. Chroma eases towards the ends of the ramp and every step
  is clamped into sRGB by lowering chroma, which keeps the hue and the lightness
  where a clip would move both.
- **Tints.** `--accent-soft`, `--green-soft`, `--amber-soft`, `--red-soft`: the
  role at a low alpha, left translucent so the same pill works on a card, on a
  field and over a wallpaper.
- **`--accent-strong`.** The one accent value that has to be read as text, so it
  is not a fixed step: the ramp is walked from the base downwards (upwards on a
  dark ground) until a step clears 4.5:1 against the tint it sits on — measured
  over `--bg`, the darkest surface that tint can sit on, because a step chosen
  against a white card misses the bar two surfaces over.
- **`--accent-line`.** Ramp step 300 on a light base, 500 on a dark one.
- **`--nav-active-text/-bg/-bar`**, which follow the three above.
- **`--veil` and `--veil-plain`**, the desk's wallpaper overlay built from
  `--scrim`.
- **`--scrim-rgb`, `--glass-rgb`, `--on-wall-rgb`**, the same colours as bare
  channels for `rgba(var(--scrim-rgb), 0.5)`.

### What is a system constant

Spacing, type, motion and the fixed dimensions are not theme data. A theme
changes what Vela is coloured with, never how densely it is set.

| Scale | Values |
| --- | --- |
| `--space-1…8` | 3, 6, 8, 11, 14, 17, 22, 28 px |
| `--text-xs…3xl` | 11.5, 12.5, 14, 15, 17, 20, 25, 32 px |
| `--motion-fast/base/slow` | 120, 200, 320 ms, with `--ease` |
| dimensions | `--rail-w`, `--rail-control`, `--panel-w`, `--header-pad`, `--content-pad` |

### Legacy names

`--radius`, `--shadow-card` and `--shadow-pop` are aliases for `--radius-md`,
`--shadow-md` and `--shadow-lg`. The generator emits both, so a theme that sets
the canonical name moves the alias too. Nothing is removed: the stylesheet is
ten thousand lines and these names are its vocabulary.

## Value rules

Adapted from ServerKit's `docs/THEMING.md`, kept where they still hold.

- **Accent as a line, never a flood.** Marks, rings, bars, focus, the active
  rail bar. The exception is a filled primary button, which Vela already fills
  and which users would notice changing.
- **No pure black or white as a surface.** Every value comes from a ramp. Shade
  is the exception: ambient darkness mixed from black is a shadow, not a colour.
- **Headings sit at weight 500 and never past it.** Hierarchy is size and space.
- **Text carries 4.5:1 against its surface.** Icons, large text and chrome
  carry 3:1. The accent-on-ground pair carries 3:1.
- **Elevation is three steps from tokens.** On a light ground an edge and a soft
  ink shadow; on a dark one an edge and ambient darkness. Never stacked.

## The primitive layer

Classes in `web/src/styles/primitives/`, components in
`web/src/components/ds/`. The components are thin: none of them fetches
anything, and each takes what it draws as props.

| Component | Class | What it is for |
| --- | --- | --- |
| `Card` | `.vela-card` | The surface every widget is drawn on: icon, title, meta, body, footer |
| `Stat` | `.vela-stat` | One number with its unit, a delta and a caption |
| `Meter` | `.vela-meter` | A labelled bar |
| `Ring` | `.vela-ring` | A proportion that should read as almost-whole |
| `Bars` | `.vela-bars` | A short series, in three accent steps |
| `Sparkline` | `.vela-sparkline` | A series too long to count |
| `KeyValue` | `.vela-kv` | Label left, value right, up to eight rows |
| `Rows` | `.vela-rows` | Name, detail and a trailing note, up to eight rows |
| `Pill` | `.vela-pill` | A dot and a word, tinted by state |
| `Tag` | `.vela-tag` | A small outlined label: a category, a version |
| `SegControl` | `.vela-seg` | A segmented control on native radios |

### Tone

A primitive is told what it means rather than what colour to be:

```jsx
<Meter percent={94} label="Photos" tone="red" />
<Pill tone="cyan">Running</Pill>
```

`data-tone` sets `--tone`, `--tone-soft` and `--tone-strong`, and the primitive
reads those. The tones are `accent` (the default), `neutral`, `cyan`, `green`,
`amber` and `red`. A mark takes `--tone`; text that has to be read takes
`--tone-strong`, which is the step that clears 4.5:1 on the tint.

This is why a widget never writes a colour, and why one written today still
looks right under a theme written next year.

### Adding a widget on the recipe

1. Draw it with `ds/` components. Nothing else reaches for the classes.
2. Give it a card header: an icon in the widget's tone and a title.
3. Render `DeskEmpty` when the source has nothing, rather than a chart of zero.
4. Keep the widget's default `w`/`h` so saved boards do not reflow.

## The guards

Both run inside `npm --prefix web run check`, through `scripts/ratchet.mjs`.

| Guard | Fails when |
| --- | --- |
| `tokens` | `_tokens.scss` has drifted from the generator; a hex, `rgb()` or `hsl()` is written in any other SCSS partial; a partial marked `// tokens: spacing` writes a raw `padding`/`gap`/`margin` between 3px and 28px |
| `inline-style` | A `style={{ }}` appears in JSX outside the registered runtime-geometry sites |

A line may opt out with `// ratchet: allow tokens <reason>`, which is counted
and listed by `--report`. The escapes that exist are all art rather than
interface colour: a bot's identity colour, the two gradient wallpapers, app tile
art generated from an app id, the agent host's deliberate no-tokens policy, and
the canvas an app paints for itself.

After changing anything under `web/src/design/`, run:

```bash
node web/scripts/build-tokens.mjs
```

## Checking a visual change

```bash
node web/scripts/compare-screenshots.mjs capture .local/shots/before 17750
# make the change, rebuild
node web/scripts/compare-screenshots.mjs capture .local/shots/after 17750
node web/scripts/compare-screenshots.mjs compare .local/shots/before .local/shots/after .local/shots/diff
```

Capture both sides on the same port: the dashboard prints the address it is
served on. The clock is frozen at capture time for the same reason.

The comparison prints how far the changed pixels moved as well as how many
moved. Read both. A large soft gradient re-dithers between builds, which counts
a quarter of the desk as changed while nothing about it looks different; a max
delta of 2 says so, and a max delta of 200 says something really moved. Then
open the diff image. A percentage alone has never closed a change.

## Themes

A theme is **data, never code**: a JSON document naming canonical tokens and
their values, validated on the server and applied one property at a time in the
browser.

```json
{
  "schema_version": 1,
  "slug": "friend",
  "name": "From a friend",
  "author": "Someone",
  "version": "1.0.0",
  "description": "One line about it.",
  "bases": ["light", "dark"],
  "suggests": { "wallpaper": "paramo" },
  "tokens": { "light": { "--bg": "#eeeeee", "…": "…" } }
}
```

`slug` matches `^[a-z][a-z0-9-]{0,31}$`. `bases` is a non-empty subset of
`light` and `dark`. Each base's `tokens` holds canonical names only.

A bad **value** is dropped and named, because one bad key is not a reason to
refuse somebody's work. A broken **structural rule** refuses the document,
because a theme with no slug is not a theme with a mistake in it. Unknown
top-level keys are dropped and named, so a theme written for a newer Vela still
applies on an older one.

### What a value may be

| Kind | Accepted |
| --- | --- |
| colour | `#rgb`, `#rrggbb`, `#rrggbbaa`, `rgb()`, `rgba()`, `color-mix(in srgb, …)` |
| length | a number with `px`, `rem` or `em` |
| font | one of the stacks in `FONT_ALLOW_LIST`, and nothing else |
| shadow | `none`, or offsets and colours |
| gradient | `none`, or one or more `linear`/`radial`/`conic-gradient()` |

Refused in every value: `url(`, `@import`, `expression(`, `javascript:`, `<`,
`>`, `{`, `}`, `;`, `@`, `/*` and `\`. A value is at most 200 characters and a
theme file at most 32 KB.

`url(` is the important one. It is the only way a theme could reach the network,
and a theme that reaches the network is a theme that can tell somebody else when
this dashboard was opened and from where. The font allow-list is there for the
same reason: a face Vela does not already load would have to be fetched.

### What a theme cannot do

- **It cannot set a wallpaper.** It may `suggest` one that ships, and
  Personalise offers a "Use it" link. A picture is the user's choice.
- **It cannot reach apps.** Apps still receive `theme: "light" | "dark"` and
  nothing more.
- **It cannot be fetched.** There is no registry and no remote source. A theme
  arrives as a file the user picked, read in their own browser and posted as
  JSON — the server has no upload path at all.

### How it is applied

`web/src/design/apply.js`. Every value is set with `setProperty` on the root
element, one at a time: a value that somehow slipped past the validator can only
fail to be that one property, where a generated stylesheet could close a
declaration and open something else.

Both bases are *also* written as a scoped stylesheet, under
`[data-theme='light']` and `[data-theme='dark']`. That is not redundant with the
inline properties. An element that re-asserts a base — an app window's dark
title bar, a Settings preview of the base you are not in — matches
`[data-theme='…']` in the generated stylesheet directly, and a value declared on
an element beats one inherited from the root. Without the applied theme under
both selectors, such an element falls back to the stock look while everything
around it wears the chosen theme.

The applied theme is cached in `localStorage` and painted by `initTheme()`
before React mounts, so a reload opens in the colours it closed in rather than
flashing the stock look. `ThemeSync` corrects it once settings load.

**Selecting the stock theme removes every inline property** rather than writing
the stock values back. The stock look is the stylesheet, not a copy of it, so it
cannot drift from what the build produces.

### The contrast gate

`scripts/ratchets/themes.mjs`, in the check. For every bundled theme and every
base it declares:

- `--text`, `--text-dim` and `--text-faint` on `--bg-card`, `--bg-workspace`,
  `--bg-panel`, `--bg-pop` and `--bg-field`: **4.5:1**
- `--accent` on `--bg-card`, and `--cyan` on `--bg-rail`: **3:1**
- `--accent-strong` on `--accent-soft`: **4.5:1**
- `--border` against `--bg-card`: **1.3:1**, so a card's edge can be found
- the high-contrast theme's body text: **7:1**

A translucent surface is measured over the ground behind it, because that is
what the eye sees.

**A bundled theme that fails does not ship** — `build-themes.mjs` writes nothing
while the gate is red. `test-shared-ui.mjs` measures the same pairs again in a
browser, because the ratchet proves the numbers and only the browser proves the
engine resolves them. An *imported* theme is the user's own choice on their own
computer; it is not held to this.

### Writing one

Export the theme you are using from Personalise, edit its colours, import it
back. The review sheet shows its name, its author, what it says about itself and
how many colours it sets before any of it is applied.

Bundled themes are not written by hand at all: `web/scripts/build-themes.mjs`
derives each one's surfaces, borders and inks from a single ground colour, at
the lightness depths measured from the stock theme, so a new theme picks a hue
and inherits Vela's own sense of depth.
