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
import { useMemo } from 'react';
import { CORE_WIDGET_TYPES } from './types.jsx';

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
  // A type may name itself from its own configuration -- a Volume widget is
  // called after the volume it points at, so a desk with three of them does
  // not read "Volume" three times. Until it has been pointed at one there is
  // nothing to derive, and falling through to the type's name is what keeps
  // the frame from having no accessible name at all.
  const derived = type?.title ? type.title(cfg) : '';
  return derived || type?.name || widget?.type || 'Widget';
}

/**
 * Every widget type the desk can place right now: Vela's own, plus one per
 * widget each installed app declares in its manifest.
 *
 * App types are namespaced, so a board keeps pointing at the right app's
 * widget, and they disappear from the library the moment the app is
 * uninstalled — a board that still names one renders the "no longer
 * available" note rather than crashing.
 */
export function useWidgetTypes(apps, appRender) {
  return useMemo(() => {
    const types = [...CORE_WIDGET_TYPES];
    const seen = new Set(types.map((type) => type.id));
    for (const app of apps || []) {
      if (!app?.installed || !Array.isArray(app.widgets)) continue;
      for (const declared of app.widgets) {
        if (!declared || typeof declared.id !== 'string') continue;
        const id = appWidgetTypeId(app.id, declared.id);
        if (seen.has(id)) continue;
        const [w, h] = APP_WIDGET_SIZES[declared.size] || APP_WIDGET_SIZES.m;
        seen.add(id);
        types.push({
          id,
          name: declared.name || declared.id,
          icon: null,
          cat: app.name,
          desc: `From ${app.name}`,
          w,
          h,
          min: [1, 1],
          defaultCfg: {},
          app,
          widgetId: declared.id,
          layout: declared.layout,
          // The size the app asked for, not the cells it got: a chart drawn
          // one cell across is a line rather than a row of slivers, and only
          // the declaration says which the app meant.
          size: declared.size,
          render: appRender,
        });
      }
    }
    return types;
  }, [apps, appRender]);
}
