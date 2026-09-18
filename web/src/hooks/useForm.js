import { useCallback, useMemo, useReducer, useRef } from 'react';
import {
  createFormState,
  dirtyFieldsFor,
  formReducer,
  normalizeFieldErrors,
  touchedFieldsFor,
} from './formState.js';

// The one place a page's form keeps its state.
//
// Origin: ServerKit `frontend/src/hooks/useForm.js` (MIT, same owner). It owns
// values, touched, dirty, submit-in-flight and where a server's complaint
// lands. It does not render anything: `ui/FormField` is still the field, and
// `ui/Button` already carries `pending`, which this sets.
//
// `initialValues` is read once. When the record arrives later, or after a
// successful save, call `reset(nextValues)`.
export function useForm({ initialValues = {}, validate, onSubmit } = {}) {
  const [state, dispatch] = useReducer(formReducer, initialValues, createFormState);
  // `validate` and `onSubmit` are almost always inline arrows, re-created every
  // render. Held in refs, they can change without `handleSubmit` changing, so a
  // form's submit button does not re-render the tree under it every keystroke.
  const validateRef = useRef(validate);
  const submitRef = useRef(onSubmit);
  const initialRef = useRef(state.initialValues);
  const valuesRef = useRef(state.values);
  // A second submit can arrive before React has committed the disabled button,
  // and while asynchronous validation is still running. A ref, not the reducer
  // flag, because the guard has to hold within one tick.
  const inFlight = useRef(false);
  validateRef.current = validate;
  submitRef.current = onSubmit;
  valuesRef.current = state.values;

  const dirtyFields = useMemo(() => dirtyFieldsFor(state), [state]);

  const setValue = useCallback((name, value, { touch = false } = {}) => {
    dispatch({ type: 'setValue', name, value });
    if (touch) dispatch({ type: 'touch', name });
  }, []);

  const setValues = useCallback((values) => dispatch({ type: 'setValues', values }), []);
  const setFieldTouched = useCallback((name) => dispatch({ type: 'touch', name }), []);

  const handleChange = useCallback(
    (event) => {
      const { name, type, checked, value } = event.target;
      setValue(name, type === 'checkbox' ? checked : value);
    },
    [setValue],
  );

  const reset = useCallback((values) => {
    const next = values ?? initialRef.current;
    initialRef.current = next;
    dispatch({ type: 'reset', values: next });
  }, []);

  const handleSubmit = useCallback(async (event) => {
    event?.preventDefault?.();
    if (inFlight.current) return { ok: false, pending: true };
    inFlight.current = true;
    try {
      const values = valuesRef.current;
      const errors = normalizeFieldErrors(
        validateRef.current ? await validateRef.current(values) : {},
      );
      if (Object.keys(errors).length > 0) {
        // Every field is revealed, not only the ones that failed: a form that
        // shows one complaint at a time makes the person submit again to find
        // the next.
        dispatch({ type: 'serverError', error: errors, touchAll: true });
        return { ok: false, errors };
      }

      dispatch({ type: 'submitStart' });
      try {
        const value = await submitRef.current?.(values);
        dispatch({ type: 'submitEnd' });
        return { ok: true, value };
      } catch (error) {
        dispatch({ type: 'serverError', error });
        return { ok: false, error };
      }
    } finally {
      inFlight.current = false;
    }
  }, []);

  const fieldError = useCallback(
    (name) => (state.touched[name] ? state.errors[name] : undefined),
    [state.errors, state.touched],
  );

  const fieldProps = useCallback(
    (name) => ({
      name,
      value: state.values[name] ?? '',
      onChange: handleChange,
      onBlur: () => setFieldTouched(name),
    }),
    [handleChange, setFieldTouched, state.values],
  );

  return {
    values: state.values,
    errors: state.errors,
    touched: state.touched,
    formError: state.formError,
    submitting: state.submitting,
    submitCount: state.submitCount,
    dirtyFields,
    dirty: Object.keys(dirtyFields).length > 0,
    setValue,
    setValues,
    setFieldTouched,
    fieldError,
    fieldProps,
    handleChange,
    handleSubmit,
    reset,
    touchAll: useCallback(() => {
      for (const name of Object.keys(touchedFieldsFor(valuesRef.current))) {
        dispatch({ type: 'touch', name });
      }
    }, []),
  };
}

export default useForm;
