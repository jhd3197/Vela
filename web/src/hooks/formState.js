// Form state as a pure reducer.
//
// Origin: ServerKit `frontend/src/hooks/formState.js` (MIT, same owner). No
// React here on purpose: dirty, touched, submit-in-flight and where a server's
// complaint belongs are decisions about state, and they are easier to get
// right — and far easier to test — away from a component.
//
// `useForm.js` is the binding. Nothing else should need this module directly.

/** The key a message lands under when it is about the form, not one field. */
export const FORM_ERROR = '_form';

const copy = (values) => ({ ...(values || {}) });

// A message can arrive as a string, as the list of complaints about one field,
// or as a nested object when the field is itself a structure. All three have
// to end up as one line under one input.
function messageText(value) {
  if (Array.isArray(value)) return value.map(messageText).filter(Boolean).join(' ');
  if (value && typeof value === 'object') {
    return Object.values(value).map(messageText).filter(Boolean).join(' ');
  }
  return value == null ? '' : String(value);
}

export function normalizeFieldErrors(errors) {
  if (!errors || typeof errors !== 'object' || Array.isArray(errors)) return {};
  return Object.fromEntries(
    Object.entries(errors)
      .map(([field, value]) => [field, messageText(value)])
      .filter(([, message]) => Boolean(message)),
  );
}

/**
 * What a failed submit means for the form: which fields it names, and what to
 * say when it names none.
 *
 * A caller can throw a plain `{ field: message }` map, or an `ApiError`. The
 * engine does not yet return per-field details (see the engine plan); when it
 * does, `error.details` and `error.fields` are already read here.
 */
export function mapFormError(error, fallback = 'Could not save your changes.') {
  if (typeof error === 'string') return { fieldErrors: {}, formError: error };
  if (error instanceof Error) {
    const fields = normalizeFieldErrors(error.fields || error.details?.fields || error.details);
    const { [FORM_ERROR]: formLevel, ...fieldErrors } = fields;
    return { fieldErrors, formError: formLevel || error.message || fallback };
  }
  const fields = normalizeFieldErrors(error);
  const { [FORM_ERROR]: formLevel, ...fieldErrors } = fields;
  return {
    fieldErrors,
    formError: formLevel || (Object.keys(fieldErrors).length ? '' : fallback),
  };
}

export function createFormState(initialValues = {}) {
  const values = copy(initialValues);
  return {
    initialValues: values,
    values,
    touched: {},
    errors: {},
    formError: '',
    submitCount: 0,
    submitting: false,
  };
}

export function formReducer(state, action) {
  switch (action.type) {
    // Typing in a field answers the complaint about that field. Leaving the
    // message up while the value changes under it is how a form ends up
    // showing an error about something the user has already fixed.
    case 'setValue':
      return {
        ...state,
        values: { ...state.values, [action.name]: action.value },
        errors: { ...state.errors, [action.name]: undefined },
        formError: '',
      };
    case 'setValues':
      return { ...state, values: { ...state.values, ...action.values } };
    case 'touch':
      return { ...state, touched: { ...state.touched, [action.name]: true } };
    case 'submitStart':
      return {
        ...state,
        errors: {},
        formError: '',
        submitting: true,
        submitCount: state.submitCount + 1,
      };
    case 'submitEnd':
      return { ...state, submitting: false };
    case 'serverError': {
      const { fieldErrors, formError } = mapFormError(action.error, action.fallback);
      return {
        ...state,
        errors: fieldErrors,
        // A field nobody has visited still shows the server's complaint about
        // it: the server has seen the value, which is what `touched` stands in
        // for the rest of the time.
        touched: action.touchAll
          ? touchedFieldsFor(state.values)
          : { ...state.touched, ...touchedFieldsFor(fieldErrors) },
        formError,
        submitting: false,
      };
    }
    case 'reset':
      return createFormState(action.values ?? state.initialValues);
    default:
      return state;
  }
}

export function dirtyFieldsFor(state) {
  return Object.fromEntries(
    Object.keys({ ...state.initialValues, ...state.values })
      .filter((name) => !Object.is(state.values[name], state.initialValues[name]))
      .map((name) => [name, true]),
  );
}

export function touchedFieldsFor(values) {
  return Object.fromEntries(Object.keys(values || {}).map((name) => [name, true]));
}
