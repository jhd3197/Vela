// Every keyboard shortcut the shell answers, written down once.
//
// The list is what the shortcut sheet draws, what a menu item shows beside its
// label, and what the listeners match against, so a shortcut cannot be added in
// one of those places and forgotten in the others. No React and no DOM: whether
// a key press means something is a question a test can ask without a browser.
//
// Keys are matched by `event.code` — the physical key — wherever a letter is
// involved, because with Alt or Option held `event.key` is whatever character
// that layout produces (Option+D is "∂" on a Mac). Punctuation that moves
// between layouts is matched by `key` instead, which is what the existing
// Ctrl+/ always did.
//
// The browser reserves some combinations outright (Ctrl+W, Ctrl+T, Ctrl+Tab)
// and the operating system takes others before a page sees them (Alt+Tab, the
// Windows key). Window shortcuts therefore use Alt, which neither claims for
// these keys, and they yield to a text field, where Alt+arrows move by word.
//
// A key pressed inside an app goes to the app's own document, not to Vela, so
// none of these fire while focus is inside an app window. Every one of them is
// also on a menu, a button or the rail.

/** The event anything can send to open the shortcut sheet. */
export const OPEN_SHORTCUTS = 'vela:shortcut-sheet';

/** Ctrl on Windows and Linux, ⌘ on a Mac. */
export const MOD = 'mod';

// Each entry: `combo` is what is pressed (`mod`, `alt`, `shift`, and a `code`
// or a `key`), `through` closes a range and `also` is a second way to press
// it. `plain` marks a shortcut that yields to a text field, where the same
// keys mean something to the field.

export const SHORTCUTS = [
  // ---- anywhere
  {
    id: 'search',
    group: 'General',
    combo: { mod: true, code: 'KeyK' },
    label: 'Search apps, notes and settings',
  },
  {
    id: 'all-apps',
    group: 'General',
    combo: { mod: true, code: 'Space' },
    label: 'Open or close All apps',
  },
  {
    id: 'open-pinned',
    group: 'General',
    combo: { mod: true, key: '1' },
    through: { mod: true, key: '9' },
    label: 'Open a pinned app by its place on the rail',
  },
  {
    id: 'next-desktop',
    group: 'Desktops',
    combo: { alt: true, shift: true, code: 'ArrowRight' },
    label: 'Next desktop',
    plain: true,
  },
  {
    id: 'previous-desktop',
    group: 'Desktops',
    combo: { alt: true, shift: true, code: 'ArrowLeft' },
    label: 'Previous desktop',
    plain: true,
  },
  {
    id: 'desktop-overview',
    group: 'Desktops',
    combo: { alt: true, shift: true, code: 'ArrowUp' },
    label: 'See every desktop',
    plain: true,
  },
  {
    id: 'lock',
    group: 'General',
    combo: { alt: true, shift: true, code: 'KeyL' },
    label: 'Lock Vela (when an app lock is set up)',
    plain: true,
  },
  {
    id: 'shortcuts',
    group: 'General',
    combo: { mod: true, key: '/' },
    also: { key: '?' },
    label: 'Show this list',
  },
  {
    id: 'dismiss',
    group: 'General',
    combo: { code: 'Escape' },
    label: 'Close All apps, a menu or a dialog',
    sheetOnly: true,
  },

  // ---- the windows on the desk
  {
    id: 'switch-window',
    group: 'Windows',
    combo: { alt: true, code: 'Backquote' },
    label: 'Switch windows (keep Alt held to choose)',
    plain: true,
  },
  {
    id: 'switch-window-back',
    group: 'Windows',
    combo: { alt: true, shift: true, code: 'Backquote' },
    label: 'Switch windows, backwards',
    plain: true,
  },
  {
    id: 'maximize',
    group: 'Windows',
    combo: { alt: true, code: 'ArrowUp' },
    label: 'Maximize the window',
    plain: true,
  },
  {
    id: 'minimize',
    group: 'Windows',
    combo: { alt: true, code: 'ArrowDown' },
    label: 'Restore a maximized window, or minimize it',
    plain: true,
  },
  {
    id: 'snap-left',
    group: 'Windows',
    combo: { alt: true, code: 'ArrowLeft' },
    label: 'Move the window to the left half',
    plain: true,
  },
  {
    id: 'snap-right',
    group: 'Windows',
    combo: { alt: true, code: 'ArrowRight' },
    label: 'Move the window to the right half',
    plain: true,
  },
  {
    id: 'snap-layouts',
    group: 'Windows',
    combo: { alt: true, code: 'KeyZ' },
    label: 'Choose a layout for the window',
    plain: true,
  },
  {
    id: 'close-window',
    group: 'Windows',
    combo: { alt: true, shift: true, code: 'KeyW' },
    label: 'Close the window',
    plain: true,
  },
  {
    id: 'show-desktop',
    group: 'Windows',
    combo: { alt: true, shift: true, code: 'KeyD' },
    label: 'Show the desktop, and bring the windows back',
    plain: true,
  },
];

const BY_ID = new Map(SHORTCUTS.map((entry) => [entry.id, entry]));

/** One shortcut by its id, or null. */
export function shortcut(id) {
  return BY_ID.get(id) || null;
}

/** Whether a keydown is this combination, with exactly these modifiers. */
export function matchesCombo(event, combo) {
  if (!event || !combo) return false;
  const mod = Boolean(event.ctrlKey || event.metaKey);
  if (Boolean(combo.mod) !== mod) return false;
  if (Boolean(combo.alt) !== Boolean(event.altKey)) return false;
  // `key` matches a character, and Shift is part of how that character was
  // typed — "?" is Shift+/ — so Shift is only checked against a code.
  if (combo.key) return event.key === combo.key;
  if (Boolean(combo.shift) !== Boolean(event.shiftKey)) return false;
  return event.code === combo.code;
}

/** Whether a keydown is the shortcut with this id, in any of its forms. */
export function matches(event, id) {
  const entry = BY_ID.get(id);
  if (!entry) return false;
  return matchesCombo(event, entry.combo) || matchesCombo(event, entry.also);
}

/** Whether this machine labels its modifiers the Mac way. */
export function isMac(platform) {
  const said =
    platform ??
    (typeof navigator === 'undefined'
      ? ''
      : navigator.userAgentData?.platform || navigator.platform || navigator.userAgent || '');
  return /mac|iphone|ipad/i.test(String(said));
}

const CODE_NAMES = {
  ArrowUp: '↑',
  ArrowDown: '↓',
  ArrowLeft: '←',
  ArrowRight: '→',
  Backquote: '`',
  Space: 'Space',
  Escape: 'Esc',
};

/** The name of the key itself, from a code or a character. */
function keyName(combo) {
  if (combo.key) return combo.key.length === 1 ? combo.key.toUpperCase() : combo.key;
  if (CODE_NAMES[combo.code]) return CODE_NAMES[combo.code];
  if (/^Key[A-Z]$/.test(combo.code)) return combo.code.slice(3);
  if (/^Digit\d$/.test(combo.code)) return combo.code.slice(5);
  return combo.code;
}

/** The keys of a combination, in the order they are held, labelled for this platform. */
export function comboKeys(combo, { mac = isMac() } = {}) {
  if (!combo) return [];
  const keys = [];
  if (combo.mod) keys.push(mac ? '⌘' : 'Ctrl');
  if (combo.alt) keys.push(mac ? '⌥' : 'Alt');
  if (combo.shift && !combo.key) keys.push(mac ? '⇧' : 'Shift');
  keys.push(keyName(combo));
  return keys;
}

/** A shortcut as one short string, for the side of a menu item: "Alt+↑", "⌥↑". */
export function comboLabel(idOrCombo, { mac = isMac() } = {}) {
  const combo = typeof idOrCombo === 'string' ? BY_ID.get(idOrCombo)?.combo : idOrCombo;
  if (!combo) return '';
  return comboKeys(combo, { mac }).join(mac ? '' : '+');
}

/** The groups of the sheet, in order, each with its shortcuts. */
export function sheetGroups() {
  const groups = [];
  for (const entry of SHORTCUTS) {
    let group = groups.find((candidate) => candidate.name === entry.group);
    if (!group) {
      group = { name: entry.group, shortcuts: [] };
      groups.push(group);
    }
    group.shortcuts.push(entry);
  }
  return groups;
}

/** Whether a key press landed somewhere that types: a field, a select, an editor. */
export function isEditable(target) {
  if (!target) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return Boolean(target.isContentEditable);
}
