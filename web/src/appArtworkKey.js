// Which mark an app gets. Kept apart from the drawings themselves so the
// decision can be tested directly, and so adding artwork never means touching
// the resolution rules.
//
// Order, most specific first: the app's own id, a connected service's initial,
// its category, then the generic mark.

export const ID_MARKS = new Set([
  'notes',
  'meals',
  'health',
  'finance',
  'system-info',
  'hello-vela',
  'ollama',
]);

export const CATEGORY_MARKS = new Set([
  'productivity',
  'wellness',
  'lifestyle',
  'finance',
  'utilities',
  'getting-started',
  'developer',
  'connected',
]);

export const CONNECTED_PREFIX = 'web--';

// A connected service is named by the person who added it, so its initial
// identifies it. Take the first letter or digit rather than the first
// character, so a service named "· Home" still resolves to "H".
export function monogram(name) {
  const match = String(name || '').match(/[\p{L}\p{N}]/u);
  return match ? match[0].toUpperCase() : null;
}

export function isConnected(app) {
  return (
    String(app?.category || '').toLowerCase() === 'connected' ||
    String(app?.id || '').startsWith(CONNECTED_PREFIX)
  );
}

export function artworkKey(app) {
  if (!app) return { kind: 'unknown' };
  if (ID_MARKS.has(app.id)) return { kind: 'id', key: app.id };

  if (isConnected(app)) {
    const letter = monogram(app.name);
    if (letter) return { kind: 'monogram', key: letter };
  }

  // Manifests are free to capitalise a category, so match case-insensitively.
  const category = String(app.category || '').toLowerCase();
  if (CATEGORY_MARKS.has(category)) return { kind: 'category', key: category };
  return { kind: 'unknown' };
}
