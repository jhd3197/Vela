// What has focus, as one answer.
//
// "The focused app" means two different things in Vela and always has. On the
// desk it means the selected window — `views.layout.selectedView`, which the
// server persists. Everywhere else it means the route you are looking at, and
// the selected window is a window on a desk you are not currently on. A bar
// that read only the first would name a calculator while you were reading the
// Marketplace; a bar that read only the second could never name a window.
//
// So the precedence is recorded once, here, and both meanings go through it
// (decision D02 in `plans/TOP-BAR-PROGRESS.md`): **the route wins off the
// desk.** On the desk, the selected window wins, and "Desk" is what is left
// when nothing is open.
//
// No React, no DOM, no fetch, and no import of `navigation.js` — that module
// pulls in every page component, and a rule that can only be checked by
// rendering the dashboard is a rule nobody checks. The page table is passed
// in; `TopBar.jsx` is the one caller that supplies the real one.

/** The routes that are the desk itself, where a window may take the name. */
const DESK_PATHS = ['/', '/desktops'];

/** Nothing is open and no page claims a name. */
export const VELA = { kind: 'vela', id: null, appId: null, viewId: null, name: 'Vela' };

/** The first path segment, or '/' for the root. */
function head(pathname) {
  const path = typeof pathname === 'string' ? pathname : '/';
  const [, first = ''] = path.split('?')[0].split('#')[0].split('/');
  return `/${first}`;
}

/** Is this route the desk, where a selected window is what has focus? */
export function isDeskRoute(pathname) {
  return DESK_PATHS.includes(head(pathname));
}

/**
 * The page a route belongs to, matched on its first segment.
 *
 * Matching the segment rather than the whole path is what makes a child route
 * keep its parent's name: `/ask/42` is still Ask, and `/automations/nightly`
 * is still Automations.
 */
export function pageFor(pathname, pages = []) {
  const segment = head(pathname);
  return pages.find((page) => head(page.to) === segment) || null;
}

/** What to call a view whose app is not in the list — or is not an app. */
function viewName(view, apps) {
  if (view.title) return view.title;
  if (view.kind === 'app') {
    return apps?.find((app) => app.id === view.appId)?.name || 'App';
  }
  if (view.kind === 'host') return view.surface === 'library' ? 'Marketplace' : 'Ask';
  if (view.kind === 'agent') return 'Agent';
  if (view.kind === 'web') {
    try {
      return new URL(view.url).hostname;
    } catch {
      return 'Web';
    }
  }
  return 'Agent';
}

/**
 * Resolve what has focus.
 *
 * `pathname` is the current route. `views` is `useDesktops().views` — its
 * `ordered` list and its `layout.selectedView` — or anything with that shape.
 * `apps` is the installed-app summaries, used only to turn an app id into a
 * name. `appId` names the app on an `/app/:id` route, which the resolver
 * cannot read out of the path because that path is not in the page table.
 * `pages` is `dashboardPages`.
 *
 * Returns `{ kind, id, appId, viewId, name }`. `appId` is the app whose menus
 * belong in the bar, and is null when what has focus is not an app.
 */
export function resolveFocus({
  pathname = '/',
  views = null,
  apps = null,
  appId = null,
  pages = [],
} = {}) {
  // An app's own full-screen page. It is the app, whatever is on the desk.
  if (appId) {
    const app = apps?.find((entry) => entry.id === appId);
    return { kind: 'app', id: appId, appId, viewId: null, name: app?.name || 'App' };
  }

  if (isDeskRoute(pathname)) {
    const selected = views?.layout?.selectedView || null;
    const open = Array.isArray(views?.ordered) ? views.ordered : [];
    // A minimized window is a window nobody is looking at. It can still be the
    // selected one — minimizing does not deselect — so the bar would otherwise
    // keep naming an app that is not on screen.
    const view = open.find((entry) => entry.id === selected && !entry.window?.minimized) || null;
    if (view) {
      return {
        kind: 'view',
        id: view.id,
        appId: view.kind === 'app' ? view.appId : null,
        viewId: view.id,
        name: viewName(view, apps),
      };
    }
    return { kind: 'desk', id: null, appId: null, viewId: null, name: 'Desk' };
  }

  const page = pageFor(pathname, pages);
  if (page) {
    // A core page is one of Vela's own tools and carries the id the rest of
    // the dashboard already knows it by; an ordinary destination has only its
    // label, which is still the right thing to call it.
    return {
      kind: 'page',
      id: page.core ? page.id || null : null,
      appId: null,
      viewId: null,
      name: page.label,
    };
  }

  return { ...VELA };
}
