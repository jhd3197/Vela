/**
 * Opening an app, and keeping the wallpaper while you do it.
 *
 * Two rules that used to be assumptions, and were wrong in the same place — the
 * desk with All apps floating over it.
 *
 * **A window is the ordinary answer, and the page is a real answer.** Opening
 * an app puts it in a window on the desk. The full-screen page is not a failure
 * path: it is what a phone gets, what an app that opens outside Vela gets, and
 * what an app that is not installed here gets. Each of those is decided here,
 * with no React and no DOM in it, so the decision can be checked without a
 * browser and cannot quietly become "whatever the last branch did".
 *
 * **Two pages can want the same wallpaper at once.** All apps opens over the
 * desk rather than replacing it, so both are mounted and both are asking for
 * the picture. The version that wrote the flags straight onto the body cleared
 * them when the Launchpad closed, and took the desk's wallpaper with it. The
 * claim stack is what makes closing the top one give the picture back to
 * whoever is still asking, and that is what these check.
 */

import assert from 'node:assert/strict';
import { beforeEach, describe, test } from 'node:test';

import { PAGE, WINDOW, presentationFor, windowFor } from '../web/src/desktops/open-app.js';
import {
  claimWallpaper,
  releaseWallpaper,
  wallpaperClaim,
  wallpaperFlags,
} from '../web/src/desk/wallpaper.js';
import {
  collapseTransform,
  fallbackStyle,
  MIN_SCALE,
} from '../web/src/desktops/motion/genie-fallback.js';
import { placeView, restorePatch } from '../web/src/desktops/window-state.js';

const DESKTOP = 'desk-1';
const embedded = (extra = {}) => ({
  id: 'finance',
  name: 'Finance',
  installed: true,
  supported: true,
  view: { surface: 'embedded', chrome: 'compact' },
  ...extra,
});

describe('where an app opens', () => {
  test('an installed embedded app gets a window', () => {
    const { presentation } = presentationFor(embedded(), {
      desktopId: DESKTOP,
    });
    assert.equal(presentation, WINDOW);
  });

  test('a phone gets the page, because a window there is the page with a bar on top', () => {
    const { presentation, reason } = presentationFor(embedded(), {
      desktopId: DESKTOP,
      narrow: true,
    });
    assert.equal(presentation, PAGE);
    assert.match(reason, /narrow/);
  });

  test('an app that opens outside Vela gets the page', () => {
    for (const surface of ['external', 'none']) {
      const { presentation } = presentationFor(embedded({ view: { surface } }), {
        desktopId: DESKTOP,
      });
      assert.equal(presentation, PAGE, surface);
    }
  });

  test('a connected site gets the page, with its own connection notice', () => {
    const { presentation } = presentationFor(embedded({ kind: 'connected-web' }), {
      desktopId: DESKTOP,
    });
    assert.equal(presentation, PAGE);
  });

  test('an app that is not installed here gets the page that can say so', () => {
    assert.equal(presentationFor(null, { desktopId: DESKTOP }).presentation, PAGE);
    assert.equal(
      presentationFor(embedded({ installed: false }), { desktopId: DESKTOP }).presentation,
      PAGE,
    );
    assert.equal(
      presentationFor(embedded({ supported: false }), { desktopId: DESKTOP }).presentation,
      PAGE,
    );
  });

  test('no desktop means nowhere to put a window', () => {
    const { presentation, reason } = presentationFor(embedded(), {
      desktopId: null,
    });
    assert.equal(presentation, PAGE);
    assert.match(reason, /desktop/);
  });

  test('every page answer says why', () => {
    for (const options of [{ desktopId: null }, { desktopId: DESKTOP, narrow: true }]) {
      assert.ok(presentationFor(embedded(), options).reason);
    }
    assert.equal(presentationFor(embedded(), { desktopId: DESKTOP }).reason, null);
  });
});

describe('an app that is already open', () => {
  const views = [
    { id: 'v1', kind: 'app', appId: 'finance', available: true },
    { id: 'v2', kind: 'agent' },
    { id: 'v3', kind: 'app', appId: 'health', available: false },
  ];

  test('is found rather than opened a second time', () => {
    assert.equal(windowFor(views, 'finance').id, 'v1');
  });

  test('a window whose installation was replaced does not count', () => {
    // That window cannot be reconnected, so opening the app means a new one
    // rather than bringing a dead window forward.
    assert.equal(windowFor(views, 'health'), null);
  });

  test('an app with no window is opened', () => {
    assert.equal(windowFor(views, 'notes'), null);
    assert.equal(windowFor(null, 'notes'), null);
  });
});

describe('the genie for a window with no picture of itself', () => {
  const bounds = { x: 200, y: 100, width: 800, height: 600 };
  const icon = { x: 12, y: 300, width: 40, height: 40 };

  test('lands the window centre on the icon centre', () => {
    const transform = collapseTransform(bounds, icon);
    // 12 + 20 - (200 + 400) = -568; 300 + 20 - (100 + 300) = -80.
    assert.match(transform, /translate\(-568px, -80px\)/);
  });

  test('shrinks to the icon, and never to nothing', () => {
    assert.match(collapseTransform(bounds, icon), /scale\(0\.05, 0\.067\)/);
    const tiny = collapseTransform({ x: 0, y: 0, width: 4000, height: 4000 }, icon);
    const [, x, y] = tiny.match(/scale\(([\d.]+), ([\d.]+)\)/);
    assert.ok(Number(x) >= MIN_SCALE && Number(y) >= MIN_SCALE);
  });

  test('nothing to aim at is null, not a guess', () => {
    assert.equal(collapseTransform(bounds, null), null);
    assert.equal(collapseTransform(null, icon), null);
    assert.equal(collapseTransform({ x: 0, y: 0, width: 0, height: 0 }, icon), null);
  });

  test('a transition needs somewhere to leave before it has somewhere to arrive', () => {
    const run = { collapsed: 'T', direction: 'collapse', durationMs: 480 };
    const start = fallbackStyle({ ...run, phase: 'start' });
    const end = fallbackStyle({ ...run, phase: 'end' });
    // The first commit is where it already is, and must not animate to itself.
    assert.equal(start.transition, 'none');
    assert.equal(start.transform, 'translate(0px, 0px) scale(1, 1)');
    assert.equal(end.transform, 'T');
    assert.match(end.transition, /480ms/);
  });

  test('coming back is the same motion the other way round', () => {
    const run = { collapsed: 'T', direction: 'expand', durationMs: 480 };
    // Restoring starts *at* the icon and is released to the window's own place.
    assert.equal(fallbackStyle({ ...run, phase: 'start' }).transform, 'T');
    assert.equal(
      fallbackStyle({ ...run, phase: 'end' }).transform,
      'translate(0px, 0px) scale(1, 1)',
    );
  });
});

describe('what a window comes back to', () => {
  const area = { width: 1440, height: 900 };
  const layout = { arrangement: 'floating', dividerRatio: 0.5 };
  const away = {
    id: 'v1',
    window: { minimized: true, restoreBounds: { x: 120, y: 80, width: 900, height: 620 } },
  };

  test('a minimized window is placed nowhere, which is what minimized means', () => {
    assert.equal(placeView(away, { layout, area, index: 0 }), null);
  });

  test('so the motion that brings it back asks where it is going instead', () => {
    // Asking the arrangement would answer "nowhere", and a window flying out of
    // nowhere does not fly at all — which is how restoring lost its motion.
    const bounds = restorePatch(away, area, 0).bounds;
    assert.deepEqual(bounds, away.window.restoreBounds);
    assert.ok(collapseTransform(bounds, { x: 12, y: 300, width: 40, height: 40 }));
  });
});

describe('who the wallpaper belongs to', () => {
  const desk = { id: 'd', page: 'desk' };
  const overlay = { id: 'o', page: 'launchpad' };
  const flags = (wallpaper) => wallpaperFlags({ wallpaper, dim: true });

  beforeEach(() => {
    releaseWallpaper(desk);
    releaseWallpaper(overlay);
  });

  test('a desk with no stored choice still has a picture', () => {
    assert.ok(wallpaperFlags(null).id);
    assert.equal(wallpaperFlags({ wallpaper: 'paramo' }).tone, 'dark');
    assert.equal(wallpaperFlags({ dim: false }).dim, 'off');
  });

  test('the newest claim is what is drawn', () => {
    claimWallpaper(desk, flags('choroni'));
    claimWallpaper(overlay, flags('avila'));
    assert.equal(wallpaperClaim().id, 'avila');
  });

  test('closing the page on top gives the picture back rather than taking it', () => {
    // This is the bug: All apps floated over the desk, and closing it cleared
    // the flags the desk underneath was still asking for.
    claimWallpaper(desk, flags('choroni'));
    claimWallpaper(overlay, flags('choroni'));
    releaseWallpaper(overlay);
    assert.equal(wallpaperClaim()?.id, 'choroni');
  });

  test('nothing is cleared until nobody is asking', () => {
    claimWallpaper(desk, flags('choroni'));
    claimWallpaper(overlay, flags('choroni'));
    releaseWallpaper(overlay);
    releaseWallpaper(desk);
    assert.equal(wallpaperClaim(), null);
  });

  test('changing a wallpaper is the same owner asking for something else', () => {
    claimWallpaper(desk, flags('choroni'));
    claimWallpaper(desk, flags('canaima'));
    assert.equal(wallpaperClaim().id, 'canaima');
    // Not a second claim underneath: releasing once releases it.
    releaseWallpaper(desk);
    assert.equal(wallpaperClaim(), null);
  });

  test('an owner that leaves out of order does not disturb the other', () => {
    claimWallpaper(desk, flags('choroni'));
    claimWallpaper(overlay, flags('avila'));
    releaseWallpaper(desk);
    assert.equal(wallpaperClaim().id, 'avila');
  });

  test('releasing something that never claimed changes nothing', () => {
    claimWallpaper(desk, flags('choroni'));
    releaseWallpaper({ id: 'stranger' });
    assert.equal(wallpaperClaim().id, 'choroni');
  });
});
