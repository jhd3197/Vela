/**
 * The shell's keyboard shortcuts, checked without a browser.
 *
 * One list drives the listeners, the shortcut sheet and the hints beside menu
 * items, so these tests are about that list holding together: every combination
 * is unique, modifiers must match exactly (Alt+↑ is not Alt+Shift+↑), letters
 * are matched by physical key so Option on a Mac still works, and the labels
 * read the way each platform writes them.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import {
  SHORTCUTS,
  comboKeys,
  comboLabel,
  isEditable,
  isMac,
  matches,
  matchesCombo,
  sheetGroups,
  shortcut,
} from '../web/src/shell/keys.js';

const press = (code, modifiers = {}, key = '') => ({
  code,
  key,
  ctrlKey: false,
  metaKey: false,
  altKey: false,
  shiftKey: false,
  ...modifiers,
});

describe('the shortcut list', () => {
  test('every id is unique', () => {
    const ids = SHORTCUTS.map((entry) => entry.id);
    assert.equal(new Set(ids).size, ids.length);
  });

  test('no two shortcuts claim the same combination', () => {
    const seen = new Map();
    for (const entry of SHORTCUTS) {
      const combo = entry.combo;
      const key = JSON.stringify([
        Boolean(combo.mod),
        Boolean(combo.alt),
        Boolean(combo.shift),
        combo.code || combo.key,
      ]);
      assert.ok(!seen.has(key), `${entry.id} and ${seen.get(key)} share a combination`);
      seen.set(key, entry.id);
    }
  });

  test('window shortcuts never use Ctrl, which the browser keeps for itself', () => {
    for (const entry of SHORTCUTS.filter((item) => item.group === 'Windows')) {
      assert.ok(!entry.combo.mod, `${entry.id} uses Ctrl`);
      assert.ok(entry.combo.alt, `${entry.id} has no Alt`);
    }
  });

  test('the sheet keeps the order of the list, grouped', () => {
    const groups = sheetGroups();
    assert.deepEqual(
      groups.map((group) => group.name),
      ['General', 'Desktops', 'Windows'],
    );
    assert.equal(
      groups.reduce((total, group) => total + group.shortcuts.length, 0),
      SHORTCUTS.length,
    );
  });
});

describe('matching a key press', () => {
  test('modifiers must match exactly', () => {
    assert.ok(matches(press('ArrowUp', { altKey: true }), 'maximize'));
    assert.ok(!matches(press('ArrowUp', { altKey: true, shiftKey: true }), 'maximize'));
    assert.ok(!matches(press('ArrowUp'), 'maximize'));
    assert.ok(!matches(press('ArrowUp', { altKey: true, ctrlKey: true }), 'maximize'));
  });

  test('letters are the physical key, whatever Option typed', () => {
    // Option+Shift+D types "Î" on a Mac.
    assert.ok(matches(press('KeyD', { altKey: true, shiftKey: true }, 'Î'), 'show-desktop'));
  });

  test('Ctrl and ⌘ are the same modifier', () => {
    assert.ok(matches(press('KeyK', { ctrlKey: true }, 'k'), 'search'));
    assert.ok(matches(press('KeyK', { metaKey: true }, 'k'), 'search'));
  });

  test('a character shortcut ignores the Shift that typed it', () => {
    assert.ok(matches(press('Slash', { shiftKey: true }, '?'), 'shortcuts'));
    assert.ok(matches(press('Slash', { ctrlKey: true }, '/'), 'shortcuts'));
  });

  test('the switcher forwards and backwards are different presses', () => {
    assert.ok(matches(press('Backquote', { altKey: true }), 'switch-window'));
    assert.ok(!matches(press('Backquote', { altKey: true, shiftKey: true }), 'switch-window'));
    assert.ok(matches(press('Backquote', { altKey: true, shiftKey: true }), 'switch-window-back'));
  });

  test('nothing matches nothing', () => {
    assert.ok(!matches(press('KeyK', { ctrlKey: true }), 'no-such-shortcut'));
    assert.ok(!matchesCombo(null, { code: 'KeyK' }));
  });
});

describe('labels', () => {
  test('written the Windows way and the Mac way', () => {
    assert.deepEqual(comboKeys(shortcut('maximize').combo, { mac: false }), ['Alt', '↑']);
    assert.deepEqual(comboKeys(shortcut('maximize').combo, { mac: true }), ['⌥', '↑']);
    assert.equal(comboLabel('close-window', { mac: false }), 'Alt+Shift+W');
    assert.equal(comboLabel('close-window', { mac: true }), '⌥⇧W');
    assert.equal(comboLabel('search', { mac: false }), 'Ctrl+K');
  });

  test('a character shortcut is written as the character', () => {
    assert.equal(comboLabel(shortcut('shortcuts').also, { mac: false }), '?');
  });

  test('platforms', () => {
    assert.ok(isMac('MacIntel'));
    assert.ok(isMac('macOS'));
    assert.ok(!isMac('Win32'));
    assert.ok(!isMac('Linux x86_64'));
  });

  test('fields are where plain keys yield', () => {
    assert.ok(isEditable({ tagName: 'INPUT' }));
    assert.ok(isEditable({ tagName: 'DIV', isContentEditable: true }));
    assert.ok(!isEditable({ tagName: 'BUTTON' }));
    assert.ok(!isEditable(null));
  });
});
