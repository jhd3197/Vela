/**
 * What a window shortcut means, checked without a browser.
 *
 * The switcher has to start on the window you were in before, wrap at both
 * ends, and include windows that are minimized. Showing the desktop has to put
 * away only what is on screen and bring back exactly that, in the arrangement
 * it was in — without reaching for a window closed in the meantime.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import {
  chosenWindow,
  frontToBack,
  openSwitcher,
  restoreFromSnapshot,
  showDesktopSnapshot,
  stepSwitcher,
  vacateAllPatch,
  verticalCommand,
} from '../web/src/desktops/window-commands.js';

const view = (id, minimized = false) => ({ id, window: { minimized } });
// Back to front, the way `views.ordered` is.
const ordered = [view('a'), view('b', true), view('c')];

describe('the window switcher', () => {
  test('lists the window in front first, minimized ones included', () => {
    assert.deepEqual(
      frontToBack(ordered).map((entry) => entry.id),
      ['c', 'b', 'a'],
    );
  });

  test('starts on the window behind the front one', () => {
    const switcher = openSwitcher(ordered);
    assert.equal(chosenWindow(switcher), 'b');
  });

  test('backwards starts on the window at the back', () => {
    assert.equal(chosenWindow(openSwitcher(ordered, -1)), 'a');
  });

  test('wraps at both ends', () => {
    let switcher = openSwitcher(ordered);
    switcher = stepSwitcher(switcher, 1);
    assert.equal(chosenWindow(switcher), 'a');
    switcher = stepSwitcher(switcher, 1);
    assert.equal(chosenWindow(switcher), 'c');
    switcher = stepSwitcher(switcher, -1);
    assert.equal(chosenWindow(switcher), 'a');
  });

  test('the selected window leads, even before the stack catches up', () => {
    assert.deepEqual(
      frontToBack(ordered, 'a').map((entry) => entry.id),
      ['a', 'c', 'b'],
    );
    assert.equal(chosenWindow(openSwitcher(ordered, 1, 'a')), 'c');
    // A minimized selection is not in front of anything.
    assert.deepEqual(
      frontToBack(ordered, 'b').map((entry) => entry.id),
      ['c', 'b', 'a'],
    );
  });

  test('one window is its own choice, and none is no switcher', () => {
    assert.equal(chosenWindow(openSwitcher([view('only')])), 'only');
    assert.equal(openSwitcher([]), null);
  });
});

describe('Alt+↑ and Alt+↓', () => {
  const floating = { arrangement: 'floating' };
  const maximized = { arrangement: 'maximized', maximizedView: 'a' };

  test('up maximizes, once', () => {
    assert.equal(verticalCommand(floating, 'a', 1), 'maximize');
    assert.equal(verticalCommand(maximized, 'a', 1), null);
  });

  test('up does nothing to a window that cannot be maximized', () => {
    assert.equal(verticalCommand(floating, 'a', 1, { maximizable: false }), null);
  });

  test('down restores a maximized window and minimizes anything else', () => {
    assert.equal(verticalCommand(maximized, 'a', -1), 'restore');
    assert.equal(verticalCommand(floating, 'a', -1), 'minimize');
    assert.equal(verticalCommand(maximized, 'b', -1), 'minimize');
  });
});

describe('showing the desktop', () => {
  const split = {
    arrangement: 'split',
    primaryView: 'a',
    secondaryView: 'c',
    maximizedView: null,
    dividerRatio: 0.6,
  };

  test('remembers only what is on screen, and the arrangement it is in', () => {
    const snapshot = showDesktopSnapshot(split, ordered);
    assert.deepEqual(snapshot.viewIds, ['a', 'c']);
    assert.equal(snapshot.layout.arrangement, 'split');
    assert.equal(snapshot.layout.dividerRatio, 0.6);
  });

  test('with nothing on screen there is nothing to remember', () => {
    assert.equal(showDesktopSnapshot(split, [view('a', true)]), null);
  });

  test('puts both panes away in one patch', () => {
    assert.deepEqual(vacateAllPatch(split, ['a', 'c']), {
      arrangement: 'split',
      primaryView: null,
      secondaryView: null,
    });
    assert.equal(vacateAllPatch({ arrangement: 'floating' }, ['a']), null);
  });

  test('brings back the same windows in the same split', () => {
    const snapshot = showDesktopSnapshot(split, ordered);
    const later = [view('a', true), view('b', true), view('c', true)];
    const restored = restoreFromSnapshot(snapshot, later);
    assert.deepEqual(restored.viewIds, ['a', 'c']);
    assert.equal(restored.layout.arrangement, 'split');
    assert.equal(restored.layout.primaryView, 'a');
    assert.equal(restored.layout.secondaryView, 'c');
  });

  test('leaves out a window closed or brought back in the meantime', () => {
    const snapshot = showDesktopSnapshot(split, ordered);
    // `c` was closed; `a` is still minimized.
    const restored = restoreFromSnapshot(snapshot, [view('a', true), view('b', true)]);
    assert.deepEqual(restored.viewIds, ['a']);
    assert.equal(restored.layout.secondaryView, null);
    assert.equal(restored.layout.arrangement, 'split', 'an empty pane is still a pane');
    // Both brought back by hand: nothing left to do.
    assert.equal(restoreFromSnapshot(snapshot, [view('a'), view('c')]), null);
  });

  test('a maximized window whose view is gone comes back floating', () => {
    const snapshot = showDesktopSnapshot({ arrangement: 'maximized', maximizedView: 'c' }, [
      view('a'),
      view('c'),
    ]);
    const restored = restoreFromSnapshot(snapshot, [view('a', true)]);
    assert.equal(restored.layout.arrangement, 'floating');
    assert.equal(restored.layout.maximizedView, null);
  });
});
