/**
 * What a dragged app card carries, and what it must never carry.
 *
 * The rule under test is short: a drop is a *reference*. An app's identity
 * travels — its id, its name, whether it is installed and which installation
 * that is — and nothing else does. No markup, no token, no app state. A split
 * screen is two windows side by side, not a channel between two apps, and a
 * drag from one to the other cannot become one.
 *
 * The second rule has no code to test directly, so it is asserted on the shape:
 * a reference says what an app *is*, never what should happen to it. Dropping
 * one attaches a name to a task; it does not install, invoke or permit.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import {
  APP_ID,
  APP_REFERENCE,
  carriesApp,
  readAppReference,
  writeAppReference,
} from '../web/src/desktops/app-reference.js';

/** The part of a DataTransfer these functions actually use. */
function transfer(initial = {}) {
  const data = { ...initial };
  return {
    data,
    effectAllowed: 'none',
    get types() {
      return Object.keys(data);
    },
    setData(type, value) {
      data[type] = String(value);
    },
    getData(type) {
      return data[type] || '';
    },
  };
}

const NOTES = { id: 'notes', name: 'Notes', installed: true, installationId: 'inst-7' };

describe('putting an app on a drag', () => {
  test('the identity travels, in both the typed and the plain shape', () => {
    const dt = transfer();
    writeAppReference(dt, NOTES, 'launchpad');
    assert.deepEqual(JSON.parse(dt.getData(APP_REFERENCE)), {
      kind: 'app',
      id: 'notes',
      name: 'Notes',
      installed: true,
      installationId: 'inst-7',
      source: 'launchpad',
    });
    assert.equal(dt.getData(APP_ID), 'notes', 'the older type stays, so old drop targets work');
    assert.equal(dt.getData('text/plain'), 'Notes', 'a text field pastes the name, not the slug');
    assert.equal(dt.effectAllowed, 'copy');
  });

  test('nothing about an app except its identity is carried', () => {
    const dt = transfer();
    writeAppReference(
      dt,
      { ...NOTES, token: 'secret-token', html: '<b>state</b>', data: { note: 'private' } },
      'launchpad',
    );
    const everything = JSON.stringify(dt.data);
    assert.ok(!everything.includes('secret-token'));
    assert.ok(!everything.includes('<b>'));
    assert.ok(!everything.includes('private'));
  });

  test('a source nobody defined becomes the default rather than travelling', () => {
    const dt = transfer();
    writeAppReference(dt, NOTES, 'somewhere-else');
    assert.equal(JSON.parse(dt.getData(APP_REFERENCE)).source, 'launchpad');
  });

  test('a drag with no app on it writes nothing', () => {
    const dt = transfer();
    writeAppReference(dt, null);
    assert.deepEqual(dt.types, []);
    writeAppReference(dt, { name: 'no id' });
    assert.deepEqual(dt.types, []);
  });
});

describe('reading a drop', () => {
  test('a typed reference comes back as it went', () => {
    const dt = transfer();
    writeAppReference(dt, NOTES, 'library');
    assert.equal(carriesApp(dt), true);
    assert.deepEqual(readAppReference(dt), {
      kind: 'app',
      id: 'notes',
      name: 'Notes',
      installed: true,
      installationId: 'inst-7',
      source: 'library',
    });
  });

  test('the older plain id is still understood', () => {
    const dt = transfer({ [APP_ID]: 'notes' });
    assert.equal(carriesApp(dt), true);
    const reference = readAppReference(dt);
    assert.equal(reference.id, 'notes');
    assert.equal(reference.installed, false, 'an id alone says nothing about installation');
  });

  test('a payload Vela cannot read is not acted on', () => {
    assert.equal(readAppReference(transfer({ [APP_REFERENCE]: 'not json' })), null);
    assert.equal(readAppReference(transfer({ [APP_REFERENCE]: '{"kind":"widget"}' })), null);
    assert.equal(readAppReference(transfer({ [APP_REFERENCE]: '{"kind":"app"}' })), null);
    assert.equal(readAppReference(transfer({ [APP_REFERENCE]: '[]' })), null);
    assert.equal(readAppReference(null), null);
  });

  test('a drag carrying something else entirely is not an app', () => {
    const dt = transfer({ 'text/plain': 'hello', 'text/html': '<p>hello</p>' });
    assert.equal(carriesApp(dt), false);
    assert.equal(readAppReference(dt), null);
  });

  test('a reference names an app and never an action', () => {
    // The shape is the contract: there is no field here that could say
    // "install this", "run that" or "allow it". A drop that wanted to do any of
    // those would need a payload this deliberately does not have.
    const dt = transfer();
    writeAppReference(dt, NOTES);
    const reference = readAppReference(dt);
    for (const forbidden of ['install', 'action', 'grant', 'permission', 'run', 'submit']) {
      assert.ok(!(forbidden in reference), forbidden);
    }
  });
});
