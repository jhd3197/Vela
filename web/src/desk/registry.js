// Widget type registry for the desk.
//
// Origin: ServerKit `frontend/src/components/dashboard/widgets/registry.js`
// (MIT, same owner). The lookup and title derivation are the same idea; the
// ServerKit type catalog and its plugin-contribution merge are not copied —
// Vela's core types live in `types.jsx` and app-provided types are merged from
// the app registry, not from a plugin bundle.
//
// A widget TYPE describes a placeable kind of widget:
//   { id, name, icon, cat, desc, w, h, min: [w, h], defaultCfg, render, app? }
// A widget INSTANCE on a board is { i, type, x, y, w, h, cfg }.

/** Widget sizes an app may ask for, as [w, h] on a 6-column board. */
export const APP_WIDGET_SIZES = {
  s: [1, 1],
  m: [2, 1],
  l: [2, 2],
};

/**
 * Namespaced type id for a widget an app provides: `<appId>:<widgetId>`.
 *
 * Without the namespace two apps both shipping a `sync` widget would collide
 * and one would vanish. The namespace is also what a saved board stores in
 * `widget.type`, so a board keeps pointing at the right app's widget.
 */
export function appWidgetTypeId(appId, widgetId) {
  return `${appId}:${widgetId}`;
}

/** The app id in a namespaced type, or null for a core type. */
export function appIdOfType(typeId) {
  const at = String(typeId || '').indexOf(':');
  return at > 0 ? String(typeId).slice(0, at) : null;
}

/**
 * Pure lookup. Returns null for an unknown id so callers can render a "this
 * widget is gone" placeholder instead of crashing — which is what a board
 * saved before an app was uninstalled will hit.
 */
export function getWidgetType(types, id) {
  if (!Array.isArray(types) || !id) return null;
  return types.find((type) => type && type.id === id) || null;
}

/**
 * What a frame calls itself. Derived at render time rather than written into
 * the stored board, because a title the user never set should not appear in
 * their saved layout.
 */
export function deriveWidgetTitle(widget, type) {
  const cfg = widget?.cfg || {};
  if (cfg.title) return cfg.title;
  if (type?.title) return type.title(cfg);
  return type?.name || widget?.type || 'Widget';
}
