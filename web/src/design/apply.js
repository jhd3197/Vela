// Putting a theme on the page.
//
// Origin: ServerKit `frontend/src/contexts/ThemeContext.jsx` lines 43-80 (MIT,
// same owner). ServerKit derived its hovers and pressed states by darkening RGB
// channels; that is replaced by the OKLCH ramp in `tokens.js`, because
// darkening in sRGB moves hue and chroma as well as lightness and the ramps
// have to line up across roles.
//
// Two rules shape everything here.
//
// **Per property, never as CSS.** Every value is set with `setProperty` on the
// root element. A theme is data; the moment it is written as a stylesheet, an
// invalid value could close a declaration and open something else. Set one at a
// time, a bad value can only fail to be that one property.
//
// **The stock look is the stylesheet, not a copy of it.** Selecting `vela`
// removes every inline property rather than writing the stock values back, so
// the generated sheet shows through and cannot drift from what the build
// produces.
import { derive } from './tokens.js';
import { readLocal, writeLocal } from '../storage.js';

/** Where the applied theme waits so the first paint after a reload has it. */
const CACHE = 'vela-theme-tokens';

/** The stock theme: no inline tokens at all. */
export const STOCK = 'vela';

/**
 * Which custom properties this module has set, so `clearTheme` can remove
 * exactly those. Reading them back off the element is not enough: the generated
 * sheet declares the same names, and `removeProperty` on an inline style only
 * ever removes the inline one, but the list also has to survive a second apply
 * setting fewer tokens than the first.
 */
let applied = [];

function paint(root, tokens) {
  for (const name of applied) {
    if (!(name in tokens)) root.style.removeProperty(name);
  }
  for (const [name, value] of Object.entries(tokens)) root.style.setProperty(name, value);
  applied = Object.keys(tokens);
}

/**
 * Apply a theme's base to the document.
 *
 * `tokens` is what a theme carries for this base. Everything else -- the ramps,
 * the tints, the nav-active trio, the legacy aliases -- is derived here with
 * the same function the build uses, so a theme written by hand and the stock
 * sheet cannot disagree about what step 600 of the accent is.
 *
 * The other base is applied too, scoped under `[data-theme='dark']`, so an app
 * window asking for a dark title bar under a light base gets *this theme's*
 * dark, not the stock one's.
 */
export function applyTheme({ base, tokens, other = null } = {}) {
  const root = document.documentElement;
  if (base) root.dataset.theme = base;
  if (!tokens) return;

  paint(root, derive(tokens, base));

  // The scoped half. `setProperty` cannot write a rule, so the other base's
  // tokens go into a single generated stylesheet instead -- the one place a
  // theme becomes CSS, built from values that have already been validated on
  // the server and derived here, never from anything a theme wrote.
  scope(other && base ? derive(other, base === 'dark' ? 'light' : 'dark') : null, base);
}

const SCOPE_ID = 'vela-theme-scope';

function scope(tokens, base) {
  const existing = document.getElementById(SCOPE_ID);
  if (!tokens) {
    existing?.remove();
    return;
  }
  const selector = base === 'dark' ? "[data-theme='light']" : "[data-theme='dark']";
  const body = Object.entries(tokens)
    // Belt and braces over the server's validator: a value that somehow carried
    // a brace or a semicolon cannot reach a stylesheet from here.
    .filter(([, value]) => !/[;{}<>]/.test(value))
    .map(([name, value]) => `${name}: ${value};`)
    .join('');
  const style = existing || Object.assign(document.createElement('style'), { id: SCOPE_ID });
  style.textContent = `${selector}{${body}}`;
  if (!existing) document.head.append(style);
}

/** Remove every inline token, so the generated stylesheet shows through. */
export function clearTheme(base) {
  const root = document.documentElement;
  if (base) root.dataset.theme = base;
  for (const name of applied) root.style.removeProperty(name);
  applied = [];
  scope(null, base);
}

/**
 * Remember what is applied, so `initTheme` can paint it before React mounts.
 *
 * Without this the first paint after a reload is the stock look and the theme
 * arrives a request later, which is a flash of the wrong colours on every load.
 */
export function cacheTheme(entry) {
  try {
    writeLocal(CACHE, entry ? JSON.stringify(entry) : '');
  } catch {
    // A browser that refuses to remember still shows the theme this session.
  }
}

export function cachedTheme() {
  try {
    const raw = readLocal(CACHE);
    if (!raw) return null;
    const entry = JSON.parse(raw);
    return entry && typeof entry === 'object' && entry.tokens ? entry : null;
  } catch {
    return null;
  }
}
