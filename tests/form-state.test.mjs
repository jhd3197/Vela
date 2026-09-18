// The form reducer, without React.
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  FORM_ERROR,
  createFormState,
  dirtyFieldsFor,
  formReducer,
  mapFormError,
  normalizeFieldErrors,
  touchedFieldsFor,
} from '../web/src/hooks/formState.js';

test('a changed value is dirty, and reset makes the new record the baseline', () => {
  const initial = createFormState({ server: 'https://ntfy.sh', topic: '' });
  assert.deepEqual(dirtyFieldsFor(initial), {});

  const typed = formReducer(initial, { type: 'setValue', name: 'topic', value: 'alerts' });
  assert.deepEqual(typed.values, { server: 'https://ntfy.sh', topic: 'alerts' });
  assert.deepEqual(dirtyFieldsFor(typed), { topic: true });

  const back = formReducer(typed, { type: 'setValue', name: 'topic', value: '' });
  assert.deepEqual(dirtyFieldsFor(back), {}, 'typing a value back is not a change');

  const saved = formReducer(typed, { type: 'reset', values: typed.values });
  assert.deepEqual(dirtyFieldsFor(saved), {});
  assert.deepEqual(saved.touched, {});
  assert.equal(saved.submitCount, 0);
});

test('typing in a field clears the complaint about that field and about the form', () => {
  const state = formReducer(createFormState({ topic: '', server: '' }), {
    type: 'serverError',
    error: { topic: 'Pick a topic.', server: 'Not a web address.' },
  });
  assert.deepEqual(state.errors, { topic: 'Pick a topic.', server: 'Not a web address.' });

  const typed = formReducer(state, { type: 'setValue', name: 'topic', value: 'a' });
  assert.equal(typed.errors.topic, undefined);
  assert.equal(typed.errors.server, 'Not a web address.', 'the other field is left alone');
  assert.equal(typed.formError, '');
});

test('touch marks one field; a validation failure reveals every field', () => {
  const initial = createFormState({ topic: '', server: '' });
  assert.deepEqual(formReducer(initial, { type: 'touch', name: 'topic' }).touched, { topic: true });

  const failed = formReducer(initial, {
    type: 'serverError',
    error: { topic: 'Pick a topic.' },
    touchAll: true,
  });
  assert.deepEqual(failed.touched, { topic: true, server: true });
  assert.deepEqual(touchedFieldsFor(initial.values), { topic: true, server: true });
});

test('a server complaint about one field touches that field and nothing else', () => {
  const state = formReducer(createFormState({ topic: '', server: '' }), {
    type: 'serverError',
    error: { server: 'Not a web address.' },
  });
  assert.deepEqual(state.touched, { server: true });
  assert.equal(state.submitting, false);
});

test('submit is in flight until it ends, and each attempt is counted', () => {
  const started = formReducer(createFormState({ topic: 'a' }), { type: 'submitStart' });
  assert.equal(started.submitting, true);
  assert.equal(started.submitCount, 1);
  assert.deepEqual(started.errors, {}, 'a new attempt starts clean of the last one');

  const ended = formReducer(started, { type: 'submitEnd' });
  assert.equal(ended.submitting, false);
  assert.equal(formReducer(ended, { type: 'submitStart' }).submitCount, 2);
});

test('a message with no field becomes the form-level error', () => {
  const state = formReducer(createFormState({ topic: '' }), {
    type: 'serverError',
    error: new Error('Could not reach the Vela backend.'),
  });
  assert.deepEqual(state.errors, {});
  assert.equal(state.formError, 'Could not reach the Vela backend.');
  assert.equal(state.submitting, false);
});

test('mapFormError reads a plain map, a string, an Error and a details object', () => {
  assert.deepEqual(mapFormError({ topic: 'Pick a topic.' }), {
    fieldErrors: { topic: 'Pick a topic.' },
    formError: '',
  });

  assert.deepEqual(mapFormError('Saving failed.'), { fieldErrors: {}, formError: 'Saving failed.' });

  const plain = new Error('Request failed (400)');
  assert.deepEqual(mapFormError(plain), { fieldErrors: {}, formError: 'Request failed (400)' });

  // The shape the engine plan's contract test pins. Reading it now means the
  // dashboard needs no change on the day the engine starts sending it.
  const detailed = new Error('That web address is not usable.');
  detailed.details = { fields: { url: ['Use HTTPS.', 'Use another hostname.'] } };
  assert.deepEqual(mapFormError(detailed), {
    fieldErrors: { url: 'Use HTTPS. Use another hostname.' },
    formError: 'That web address is not usable.',
  });
});

test(`a ${FORM_ERROR} key is about the form, not a field called ${FORM_ERROR}`, () => {
  const state = formReducer(createFormState({ topic: '' }), {
    type: 'serverError',
    error: { [FORM_ERROR]: 'Notifications are switched off.', topic: 'Pick a topic.' },
  });
  assert.deepEqual(state.errors, { topic: 'Pick a topic.' });
  assert.equal(state.formError, 'Notifications are switched off.');
});

test('an empty or unusable complaint never becomes a blank error line', () => {
  assert.deepEqual(normalizeFieldErrors({ topic: '', server: null, user: [] }), {});
  assert.deepEqual(normalizeFieldErrors(null), {});
  assert.deepEqual(normalizeFieldErrors(['Pick a topic.']), {});
  assert.equal(mapFormError({}).formError, 'Could not save your changes.');
  assert.equal(mapFormError({}, 'Could not add the web app.').formError, 'Could not add the web app.');
});

test('setValues merges without disturbing what is not named', () => {
  const state = formReducer(createFormState({ topic: 'a', server: 'b', user: 'c' }), {
    type: 'setValues',
    values: { topic: 'x' },
  });
  assert.deepEqual(state.values, { topic: 'x', server: 'b', user: 'c' });
});

test('a field added after the initial values still counts as dirty', () => {
  const state = formReducer(createFormState({ topic: '' }), {
    type: 'setValue',
    name: 'pass',
    value: 'secret',
  });
  assert.deepEqual(dirtyFieldsFor(state), { pass: true });
});
