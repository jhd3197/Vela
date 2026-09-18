// How an app should be opened: as a window on the desk, or as a page.
//
// A window is the ordinary answer now. An app on a personal computer is a
// window you can move, put away and come back to while you do something else,
// and Vela's desk is the place windows live. The full-screen page is still here
// and still correct — it is what a link to `/app/<id>` gives you, what a phone
// gets, and what an app that does not draw inside a frame has to have — but it
// is the exception rather than the default.
//
// The decision is made here, with no React and no DOM in it, because it is the
// part that has to keep being right for an app that was uninstalled between the
// click and the answer, for a manifest that asks to open outside Vela, and for
// a browser window narrow enough that a floating window would cover everything
// underneath it anyway.

/** The two presentations. Not interchangeable, and never inferred from a guess. */
export const PAGE = 'page';
export const WINDOW = 'window';

/**
 * Where opening this app should put it.
 *
 * Returns the presentation and, when it is not a window, why — so a caller that
 * wants to say so can, instead of the app silently opening somewhere other than
 * where the person expected.
 *
 * `narrow` is the phone composition. A floating window there would be the whole
 * screen with a title bar on top, which is a worse version of the page it would
 * be standing in for; the threshold itself is `breakpoints.js`'s to own.
 */
export function presentationFor(app, { narrow = false, desktopId = null } = {}) {
  if (!desktopId) return { presentation: PAGE, reason: 'there is no desktop to put a window on' };
  if (narrow) return { presentation: PAGE, reason: 'this screen is too narrow for a window' };
  if (!app) return { presentation: PAGE, reason: 'that app is not installed here' };
  if (!app.installed || !app.supported) {
    return { presentation: PAGE, reason: 'that app is not installed here' };
  }
  // A connected site is somebody else's page, reached through its own view with
  // its own connection notice. It is not an app Vela runs in a frame.
  if (app.kind === 'connected-web') {
    return { presentation: PAGE, reason: 'a connected site opens in its own view' };
  }
  const surface = app.view?.surface || 'embedded';
  if (surface !== 'embedded') {
    return { presentation: PAGE, reason: 'that app opens outside Vela' };
  }
  return { presentation: WINDOW, reason: null };
}

/**
 * The window this app already has on the desktop, if it has one.
 *
 * Clicking an app that is already open is a request to look at it, not a
 * request for a second copy of it — the same thing a taskbar button does. An
 * app whose installation was replaced does not count: that window cannot be
 * reconnected, so opening the app means opening a new one.
 */
export function windowFor(views, appId) {
  return (
    (views || []).find(
      (view) => view.kind === 'app' && view.appId === appId && view.available !== false,
    ) || null
  );
}
