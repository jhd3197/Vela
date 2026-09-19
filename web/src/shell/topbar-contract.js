// What an app may put in the top bar's right rail, and what it may not.
//
// The mirror of `vela/topbar.py`, on the side of the contract that runs in the
// browser. Status items belong to one open window and are never stored on the
// server (decision D05 in `plans/TOP-BAR-PROGRESS.md`), so this is where they
// are checked — before the bridge accepts them, not while the bar draws them.
//
// The rules are the widget rules: data, never markup; capped counts and capped
// strings; an icon named from a closed set the host already bundles, never an
// image the app controls; a tone named from a closed set, never a colour. An
// item cannot carry a URL, because the only thing clicking one does is raise
// the window of the app that published it.
//
// No React and no DOM here: `tests/bridge.test.mjs` runs it directly.

/** At most this many items per view. Three fits beside search and the bell. */
export const MAX_ITEMS = 3;

/** An id is the app's own handle for one item, so publishing replaces it. */
export const ITEM_ID = /^[a-z][a-z0-9-]{0,31}$/;

/** What is drawn in the bar. Twelve characters is "-4°C · 12:04" wide. */
export const MAX_LABEL = 12;

/** The tooltip, which has room for a sentence. Matches the widget cap. */
export const MAX_TITLE = 200;

/** The whole published list, encoded. Generous for three items, small enough
 *  that a misbehaving app cannot make the bar expensive to hold. */
export const MAX_BYTES = 1024;

/**
 * The icons an item may name.
 *
 * Closed on purpose. An open set would be a name the host has to look up
 * somewhere, and the first thing somebody would ask for is a URL. These are
 * Phosphor marks the dashboard already ships; `TopBar.jsx` maps them to
 * components and nothing else can reach that map.
 */
export const STATUS_ICONS = [
  'battery',
  'bell',
  'calendar',
  'check',
  'clock',
  'cloud',
  'database',
  'download',
  'envelope',
  'globe',
  'heart',
  'lock',
  'moon',
  'music',
  'pulse',
  'sun',
  'thermometer',
  'upload',
  'warning',
  'wifi',
];

/** How an item is coloured. The host owns the colours; the app names a role. */
export const STATUS_TONES = ['neutral', 'positive', 'caution', 'critical'];

/** A refusal the bridge can answer with: a message and an HTTP-ish status. */
function refuse(message, status = 422) {
  return Object.assign(new Error(message), { status });
}

/**
 * Check one published list and return the shape the bar stores.
 *
 * Throws on anything unexpected rather than dropping it quietly: an app that
 * sent a field this host does not know should be told, not left believing the
 * bar is showing something it is not.
 */
export function validateItems(value) {
  if (!Array.isArray(value)) throw refuse('Top bar items are a list');
  if (value.length > MAX_ITEMS) throw refuse(`At most ${MAX_ITEMS} top bar items`);
  // Measured before the fields are read, so an oversized payload is refused
  // rather than walked.
  let encoded;
  try {
    encoded = JSON.stringify(value);
  } catch {
    throw refuse('Top bar items must be plain JSON');
  }
  if (encoded.length > MAX_BYTES) {
    throw refuse(`Top bar items are at most ${MAX_BYTES} bytes`, 413);
  }

  const seen = new Set();
  return value.map((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      throw refuse('Each top bar item is an object');
    }
    const unknown = Object.keys(item).filter(
      (key) => !['id', 'icon', 'label', 'title', 'tone'].includes(key),
    );
    if (unknown.length) throw refuse(`Unknown top bar item fields: ${unknown.sort().join(', ')}`);
    if (typeof item.id !== 'string' || !ITEM_ID.test(item.id)) {
      throw refuse('A top bar item id matches [a-z][a-z0-9-]{0,31}');
    }
    if (seen.has(item.id)) throw refuse(`Duplicate top bar item id "${item.id}"`);
    seen.add(item.id);

    const out = { id: item.id };
    if (item.icon !== undefined) {
      if (!STATUS_ICONS.includes(item.icon)) {
        throw refuse(`A top bar item icon is one of: ${STATUS_ICONS.join(', ')}`);
      }
      out.icon = item.icon;
    }
    for (const [field, cap] of [
      ['label', MAX_LABEL],
      ['title', MAX_TITLE],
    ]) {
      if (item[field] === undefined) continue;
      if (typeof item[field] !== 'string') throw refuse(`A top bar item ${field} is text`);
      if (item[field].length > cap) {
        throw refuse(`A top bar item ${field} is at most ${cap} characters`);
      }
      out[field] = item[field];
    }
    if (item.tone !== undefined) {
      if (!STATUS_TONES.includes(item.tone)) {
        throw refuse(`A top bar item tone is one of: ${STATUS_TONES.join(', ')}`);
      }
      out.tone = item.tone;
    }
    // Something has to be drawn. An item with neither a mark nor a word would
    // be an invisible thing occupying a slot in the bar.
    if (!out.icon && !out.label) throw refuse('A top bar item needs an icon or a label');
    return out;
  });
}

/** The menu actions the host will perform. Anything else is a label only. */
export const MENU_ACTIONS = ['return', 'close'];
