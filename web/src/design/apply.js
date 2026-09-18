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
 * Both bases are also written as a scoped stylesheet, so an element that
 * re-asserts a base -- an app window's dark title bar, a Settings preview of
 * the base you are not in -- gets *this theme's* colours rather than the stock
 * ones. See `scope()` for why the current base has to be in there too.
 */
export function applyTheme({ base, tokens, other = null } = {}) {
  const root = document.documentElement;
  if (base) root.dataset.theme = base;
  if (!tokens) return;

  paint(root, derive(tokens, base));

  // The scoped half. `setProperty` cannot write a rule, so the per-base tokens
  // go into a single generated stylesheet instead -- the one place a theme
  // becomes CSS, built from values already validated on the server and derived
  // here, never from anything a theme wrote.
  //
  // Both bases are written, not just the other one. An element that re-asserts
  // a base -- an app window's dark title bar, a Settings preview of the base
  // you are not in -- matches `[data-theme='…']` in the *generated* stylesheet
  // directly, and a value declared on the element beats one inherited from the
  // root. Without the current base here, such an element silently falls back to
  // the stock theme while everything around it wears the chosen one.
  const opposite = base === 'dark' ? 'light' : 'dark';
  scope({
    [base]: derive(tokens, base),
    ...(other ? { [opposite]: derive(other, opposite) } : {}),
  });
}

const SCOPE_ID = 'vela-theme-scope';

function scope(byBase) {
  const existing = document.getElementById(SCOPE_ID);
  if (!byBase) {
    existing?.remove();
    return;
  }
  const rules = Object.entries(byBase)
    .map(([base, tokens]) => {
      const body = Object.entries(tokens)
        // Belt and braces over the server's validator: a value that somehow
        // carried a brace or a semicolon cannot reach a stylesheet from here.
        .filter(([, value]) => !/[;{}<>]/.test(value))
        .map(([name, value]) => `${name}: ${value};`)
        .join('');
      return `[data-theme='${base}']{${body}}`;
    })
    .join('');
  const style = existing || Object.assign(document.createElement('style'), { id: SCOPE_ID });
  style.textContent = rules;
  if (!existing) document.head.append(style);
}

/** Remove every inline token, so the generated stylesheet shows through. */
export function clearTheme(base) {
  const root = document.documentElement;
  if (base) root.dataset.theme = base;
  for (const name of applied) root.style.removeProperty(name);
  applied = [];
  scope(null);
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
