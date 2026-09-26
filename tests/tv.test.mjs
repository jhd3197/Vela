import assert from 'node:assert/strict';
import test from 'node:test';
import { isTvForm, pickNext } from '../web/src/tv.js';

// A remote only has arrows, so which control an arrow lands on is the whole
// interface. Boxes are plain objects shaped like DOMRects.
const box = (left, top, width = 100, height = 40) => ({
  left,
  top,
  width,
  height,
  right: left + width,
  bottom: top + height,
});
const named = (name, rect) => ({ name, rect });

// A row of three buttons above a row of two, like a settings screen.
const grid = [
  named('a', box(0, 0)),
  named('b', box(120, 0)),
  named('c', box(240, 0)),
  named('d', box(0, 100)),
  named('e', box(240, 100)),
];

test('right moves along the row, not down to a nearer row', () => {
  assert.equal(pickNext(box(0, 0), grid.slice(1), 'right').name, 'b');
  assert.equal(pickNext(box(120, 0), grid, 'right').name, 'c');
});

test('down picks the control below, preferring the one in line', () => {
  assert.equal(pickNext(box(0, 0), grid.slice(1), 'down').name, 'd');
  assert.equal(pickNext(box(240, 0), grid.slice(0, 4).concat(grid[4]), 'down').name, 'e');
});

test('up and left find the way back', () => {
  assert.equal(pickNext(box(240, 100), grid.slice(0, 4), 'up').name, 'c');
  assert.equal(pickNext(box(240, 100), grid.slice(0, 4), 'left').name, 'd');
});

test('there is nothing beyond the edge', () => {
  assert.equal(pickNext(box(240, 0), grid, 'right'), null);
  assert.equal(pickNext(box(0, 0), grid, 'up'), null);
  assert.equal(pickNext(box(0, 0), [], 'down'), null);
});

test('a control that overlaps the edge still counts', () => {
  const wide = named('wide', box(80, 30, 300, 40));
  assert.equal(pickNext(box(0, 0), [wide], 'down').name, 'wide');
});

test('TV mode follows what the Vela app reports, and nothing else', () => {
  const app = (form) => ({
    VelaAndroid: { info: () => JSON.stringify({ form }) },
  });
  assert.equal(isTvForm(app('tv')), true);
  assert.equal(isTvForm(app('phone')), false);
  assert.equal(isTvForm({}), false);
  assert.equal(isTvForm({ VelaAndroid: { info: () => 'not json' } }), false);
  assert.equal(isTvForm({ VelaAndroid: { info: () => null } }), false);
  assert.equal(isTvForm(undefined), false);
});
