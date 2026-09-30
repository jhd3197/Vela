// One desktop and what is open on it, kept in memory, for fixtures that stub
// the API. On a wide screen Settings is a window on the desk, so a fixture that
// opens Settings has to answer the desk's view API. Plain JavaScript with no
// Node or browser dependencies, so both a Playwright route and a browser-side
// fetch stub can use it.
//
// `answer(method, pathname, body)` returns `{ status, json }` for any
// `/api/desktops…` request.

export const DESK_ID = 'fixturedesk';

export function createDesktopsMock() {
  const views = [];
  let stack = 0;
  const layout = {
    revision: 0,
    arrangement: 'floating',
    maximizedView: null,
    primaryView: null,
    secondaryView: null,
    dividerRatio: 0.5,
    selectedView: null,
  };
  const ok = (json, status = 200) => ({ status, json });
  const prefix = `/api/desktops/${DESK_ID}/`;

  function answer(method, pathname, body = {}) {
    if (pathname === '/api/desktops') {
      return ok({
        desktops: [{ id: DESK_ID, name: 'Desktop 1', kind: 'personal', position: 0, revision: 1 }],
        defaultId: DESK_ID,
      });
    }
    if (pathname === '/api/desktops/attention') return ok({ desktops: {} });
    if (!pathname.startsWith(prefix)) return ok({});
    const rest = pathname.slice(prefix.length);
    if (rest === 'views' && method === 'POST') {
      const view = {
        id: String(views.length + 1).padStart(32, '0'),
        desktopId: DESK_ID,
        kind: body.kind,
        surface: body.surface || null,
        appId: body.appId || null,
        title: body.title || null,
        position: views.length + 1,
        state: {},
        window: { bounds: null, restoreBounds: null, minimized: false, stack: ++stack },
        available: true,
        agentViewable: false,
      };
      views.push(view);
      return ok(view, 201);
    }
    if (rest === 'views') return ok({ views, layout });
    if (rest.startsWith('views/')) {
      const id = rest.slice('views/'.length);
      const index = views.findIndex((view) => view.id === id);
      if (index < 0) return ok({ detail: 'That view is no longer open.' }, 404);
      if (method === 'DELETE') {
        views.splice(index, 1);
        if (layout.selectedView === id) layout.selectedView = null;
        return ok({});
      }
      const view = views[index];
      for (const key of ['bounds', 'restoreBounds', 'minimized']) {
        if (key in body) view.window[key] = body[key];
      }
      if (body.raise) view.window.stack = ++stack;
      return ok(view);
    }
    if (rest === 'selected-view') {
      layout.selectedView = body.viewId || null;
      return ok(layout);
    }
    if (rest === 'layout') return ok(layout);
    if (rest === 'appearance') {
      return ok({ wallpaper: 'choroni', wallpaperAsset: null, dim: true, labels: true });
    }
    if (rest === 'boards') {
      return ok({
        revision: 0,
        boards: { version: 1, desktop: { cols: 6, widgets: [] }, phone: { cols: 2, widgets: [] } },
      });
    }
    return ok({});
  }

  return { answer, views, layout };
}
