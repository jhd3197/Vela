/**
 * What the top bar names, and what an app may put in it.
 *
 * Two rules that are easy to get wrong in opposite directions. **Focus has one
 * answer.** On the desk it is the selected window; everywhere else it is the
 * route, and a window on a desk you are not looking at is not what has focus.
 * A bar that read only the window would name a calculator while you were
 * reading the Marketplace; one that read only the route could never name a
 * window at all.
 *
 * **A published item is data.** Everything an app can put in the bar is
 * checked here before the bar ever sees it: counts, string lengths, an icon
 * from a closed set, a tone from a closed set, and nothing else. The caps
 * themselves are `vela/topbar.py`'s, and `tests/test_topbar.py` is what holds
 * the two halves to the same numbers.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, test } from 'node:test';

import {
  DOCUMENT_TITLE,
  documentTitleFor,
  isDeskRoute,
  pageFor,
  resolveFocus,
  VELA,
} from '../web/src/shell/focus.js';
import {
  MAX_BYTES,
  MAX_ITEMS,
  MAX_LABEL,
  MAX_TITLE,
  MENU_ACTIONS,
  STATUS_ICONS,
  STATUS_TONES,
  validateItems,
} from '../web/src/shell/topbar-contract.js';

// The real page table cannot be imported here: `navigation.js` pulls in every
// page component, and .jsx is not something node runs. So the routes are
// mirrored, and the last test in this block is what keeps the mirror honest.
const PAGES = [
  { to: '/', label: 'Desk', end: true },
  { to: '/apps', label: 'All apps', overlay: true },
  { to: '/ask', label: 'Ask', core: true, id: 'ask', childPaths: ['/ask/:conversationId'] },
  { to: '/library', label: 'Marketplace', core: true, id: 'library' },
  { to: '/automations', label: 'Automations', core: true, id: 'automations' },
  { to: '/files', label: 'Files', core: true, id: 'files' },
  { to: '/environments', label: 'System', core: true, id: 'system', developer: true },
  { to: '/settings', label: 'Settings', core: true, id: 'settings', popup: true },
];

const APPS = [
  { id: 'calc', name: 'Calc' },
  { id: 'notes', name: 'Notes' },
];

const window_ = (extra = {}) => ({ stack: 1, ...extra });
const view = (id, appId, extra = {}) => ({
  id,
  kind: 'app',
  appId,
  window: window_(),
  ...extra,
});

const desk = (ordered, selectedView = null) => ({ ordered, layout: { selectedView } });

const at = (pathname, views = null, extra = {}) =>
  resolveFocus({ pathname, views, apps: APPS, pages: PAGES, ...extra });

describe('what has focus', () => {
  test('an empty desk is the desk, not a window that is not there', () => {
    assert.equal(at('/', desk([], null)).name, 'Desk');
    assert.equal(at('/', desk([], null)).kind, 'desk');
    assert.equal(at('/').name, 'Desk', 'no desktop loaded yet is still the desk');
  });

  test('the selected window takes the name, and gives it up when another does', () => {
    const open = [view('v1', 'notes'), view('v2', 'calc')];
    assert.equal(at('/', desk(open, 'v1')).name, 'Notes');
    const focused = at('/', desk(open, 'v2'));
    assert.equal(focused.name, 'Calc');
    assert.equal(focused.appId, 'calc', 'the bar draws that app’s menus');
    assert.equal(focused.viewId, 'v2', 'and acts on that window');
  });

  test('a minimized window is not what you are looking at', () => {
    // Minimizing does not deselect, so the selected window can be one that is
    // no longer on screen. Naming it would point the bar at nothing.
    const open = [view('v1', 'notes', { window: window_({ minimized: true }) })];
    assert.equal(at('/', desk(open, 'v1')).name, 'Desk');
  });

  test('a window that is not an app is still named, and carries no menus', () => {
    const cases = [
      [{ id: 'v', kind: 'host', surface: 'library', window: window_() }, 'Marketplace'],
      [{ id: 'v', kind: 'host', surface: 'ask', window: window_() }, 'Ask'],
      [{ id: 'v', kind: 'agent', window: window_() }, 'Agent'],
      [{ id: 'v', kind: 'web', url: 'https://example.test/x', window: window_() }, 'example.test'],
      [{ id: 'v', kind: 'web', url: 'not a url', window: window_() }, 'Web'],
      [{ id: 'v', kind: 'app', appId: 'gone', window: window_() }, 'App'],
      [{ id: 'v', kind: 'app', appId: 'calc', title: 'Tape', window: window_() }, 'Tape'],
    ];
    for (const [entry, name] of cases) {
      const focus = at('/', desk([entry], 'v'));
      assert.equal(focus.name, name);
      if (entry.kind !== 'app') assert.equal(focus.appId, null);
    }
  });

  test('the route wins off the desk, even while a window is selected', () => {
    // The decision this file exists to pin down (D02). A selected window on a
    // desk you have navigated away from is not what has focus.
    const open = desk([view('v2', 'calc')], 'v2');
    assert.equal(at('/library', open).name, 'Marketplace');
    assert.equal(at('/ask', open).name, 'Ask');
    assert.equal(at('/ask', open).appId, null, 'a page has no app menus');
  });

  test('a child route keeps its page’s name', () => {
    assert.equal(at('/ask/2f8c').name, 'Ask');
    assert.equal(at('/automations/nightly-backup').name, 'Automations');
    assert.equal(at('/library?q=notes').name, 'Marketplace');
  });

  test('an app’s own page is the app', () => {
    const focus = at('/app/calc', null, { appId: 'calc' });
    assert.equal(focus.name, 'Calc');
    assert.equal(focus.appId, 'calc');
    assert.equal(focus.viewId, null, 'a page is not a window, so there is none to close');
    // An id the dashboard does not know is still that route's identity.
    assert.equal(at('/app/ghost', null, { appId: 'ghost' }).name, 'App');
  });

  test('a link straight to one workspace is the desk', () => {
    assert.ok(isDeskRoute('/desktops/abc'));
    assert.equal(at('/desktops/abc', desk([view('v1', 'notes')], 'v1')).name, 'Notes');
  });

  test('a route that belongs to no page is Vela', () => {
    assert.deepEqual(at('/nowhere'), VELA);
    assert.equal(at('/nowhere').kind, 'vela');
  });

  test('every route in navigation.js is one this resolver can name', () => {
    // The mirror above is a fixture, and a fixture drifts. Reading the real
    // table as text is blunt, and it is also what fails the day somebody adds
    // a destination the bar would have called "Vela".
    const source = readFileSync(
      new URL('../web/src/navigation.js', import.meta.url),
      'utf8',
    );
    const routes = [...source.matchAll(/^\s*(?:\{\s*)?to: '([^']+)'/gm)].map((m) => m[1]);
    assert.ok(routes.length >= 8, 'the page table was not read');
    for (const route of routes) {
      assert.ok(
        PAGES.some((page) => page.to === route),
        `navigation.js has ${route}, which this test's page table does not`,
      );
      assert.notEqual(at(route).kind, 'vela', `${route} falls through to the Vela fallback`);
    }
  });

  test('pageFor matches a segment, not a prefix', () => {
    assert.equal(pageFor('/files', PAGES)?.label, 'Files');
    assert.equal(pageFor('/files-of-mine', PAGES), null);
  });
});

describe('the browser tab title', () => {
  const notes = [{ id: 'notes', name: 'Notes' }];

  test('names the focused window the way the bar does', () => {
    const views = desk([view('v1', 'notes')], 'v1');
    const focus = resolveFocus({ pathname: '/', views, apps: notes });
    assert.equal(documentTitleFor(focus), 'Notes · Vela');
  });

  test('keeps the product title when nothing is in focus', () => {
    assert.equal(documentTitleFor(resolveFocus({ pathname: '/' })), DOCUMENT_TITLE);
    assert.equal(documentTitleFor(VELA), DOCUMENT_TITLE);
    assert.equal(documentTitleFor(null), DOCUMENT_TITLE);
  });

  test('a minimized window does not name the tab', () => {
    const views = desk([view('v1', 'notes', { window: { minimized: true } })], 'v1');
    const focus = resolveFocus({ pathname: '/', views, apps: notes });
    assert.equal(documentTitleFor(focus), DOCUMENT_TITLE);
  });

  test('matches the title the page ships with', () => {
    const html = readFileSync(new URL('../web/index.html', import.meta.url), 'utf8');
    assert.ok(html.includes(`<title>${DOCUMENT_TITLE}</title>`));
  });
});

describe('what an app may put in the bar', () => {
  const ok = (items) => validateItems(items);
  const refused = (items) => {
    try {
      validateItems(items);
    } catch (error) {
      return error;
    }
    return null;
  };

  test('a full item round-trips, and an empty list is a valid thing to say', () => {
    assert.deepEqual(ok([]), []);
    assert.deepEqual(
      ok([{ id: 'temp', icon: 'thermometer', label: '-4°C', title: 'Oslo, feels like -9', tone: 'caution' }]),
      [{ id: 'temp', icon: 'thermometer', label: '-4°C', title: 'Oslo, feels like -9', tone: 'caution' }],
    );
    // An icon alone, and a word alone, are both complete items.
    assert.deepEqual(ok([{ id: 'up', icon: 'check' }]), [{ id: 'up', icon: 'check' }]);
    assert.deepEqual(ok([{ id: 'q', label: '12' }]), [{ id: 'q', label: '12' }]);
  });

  test('an item with nothing to draw is refused rather than taking a slot', () => {
    assert.match(refused([{ id: 'ghost' }]).message, /needs an icon or a label/);
    assert.match(refused([{ id: 'ghost', title: 'only a tooltip' }]).message, /icon or a label/);
  });

  test('the caps are enforced', () => {
    assert.match(
      refused(Array.from({ length: MAX_ITEMS + 1 }, (_, n) => ({ id: `i${n}`, label: 'x' })))
        .message,
      new RegExp(`At most ${MAX_ITEMS} top bar items`),
    );
    assert.match(refused([{ id: 'a', label: 'x'.repeat(MAX_LABEL + 1) }]).message, /at most 12/);
    assert.match(
      refused([{ id: 'a', icon: 'sun', title: 'x'.repeat(MAX_TITLE + 1) }]).message,
      /at most 200/,
    );
    // Over the byte cap is a 413, the way an oversized widget summary is.
    const fat = refused([
      { id: 'a', title: 'x'.repeat(MAX_TITLE), label: 'aaaa' },
      { id: 'b', title: 'y'.repeat(MAX_TITLE), label: 'bbbb' },
      { id: 'c', title: 'z'.repeat(MAX_TITLE), label: 'cccc' },
    ]);
    assert.equal(fat, null, 'three full items still fit inside the cap');
    const over = refused([
      { id: 'a', label: 'x', title: 'y'.repeat(MAX_TITLE) },
      { id: 'b', label: 'x', title: 'z'.repeat(MAX_TITLE) },
      { id: 'c', label: 'x', title: 'w'.repeat(MAX_TITLE) },
      { id: 'd', label: 'x' },
    ]);
    assert.ok(over, 'a fourth item is refused whatever it weighs');
  });

  test('the size cap counts bytes, which is what it says', () => {
    // Three items inside every other limit, written in a character that takes
    // two bytes. Counted as characters this list fits; counted as the contract
    // actually promises it does not, and a cap that let it through would be a
    // cap in name only.
    const wide = ['a', 'b', 'c'].map((id) => ({ id, label: 'xx', title: '°'.repeat(MAX_TITLE) }));
    assert.ok(JSON.stringify(wide).length < MAX_BYTES, 'it fits if you count characters');
    assert.ok(new TextEncoder().encode(JSON.stringify(wide)).length > MAX_BYTES, 'and not bytes');
    assert.equal(refused(wide).status, 413);
  });

  test('an icon is chosen from the set, never supplied', () => {
    for (const icon of STATUS_ICONS) {
      assert.deepEqual(ok([{ id: 'a', icon }]), [{ id: 'a', icon }]);
    }
    for (const icon of ['rocket', 'https://evil.test/pixel.png', '<svg/>', '', 1]) {
      assert.match(refused([{ id: 'a', icon }]).message, /icon is one of/);
    }
  });

  test('a tone names a role, never a colour', () => {
    for (const tone of STATUS_TONES) {
      assert.equal(ok([{ id: 'a', label: 'x', tone }])[0].tone, tone);
    }
    assert.match(refused([{ id: 'a', label: 'x', tone: '#ff0000' }]).message, /tone is one of/);
  });

  test('nothing an app did not declare survives, and markup is not a field', () => {
    assert.match(refused([{ id: 'a', label: 'x', href: '/x' }]).message, /Unknown top bar item/);
    assert.match(refused([{ id: 'a', label: 'x', html: '<b>hi</b>' }]).message, /Unknown/);
    assert.match(refused([{ id: 'a', label: 'x', onClick: 'alert(1)' }]).message, /Unknown/);
    // A label is text and stays text; the bar renders it as a string, so
    // markup in it is a string with angle brackets in it and nothing more.
    assert.equal(ok([{ id: 'a', label: '<b>3</b>' }])[0].label, '<b>3</b>');
  });

  test('an id is a handle, and two items cannot share one', () => {
    assert.match(refused([{ id: 'Temp', label: 'x' }]).message, /id matches/);
    assert.match(refused([{ id: '', label: 'x' }]).message, /id matches/);
    assert.match(
      refused([
        { id: 'a', label: 'one' },
        { id: 'a', label: 'two' },
      ]).message,
      /Duplicate top bar item id/,
    );
  });

  test('a list is a list', () => {
    assert.match(refused({ id: 'a' }).message, /are a list/);
    assert.match(refused(null).message, /are a list/);
    assert.match(refused(['a']).message, /is an object/);
  });

  test('the menu actions the host performs are exactly two', () => {
    assert.deepEqual(MENU_ACTIONS, ['return', 'close']);
  });
});
