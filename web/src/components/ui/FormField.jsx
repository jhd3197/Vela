import { Children, cloneElement, useId } from 'react';

// A single input, select, or textarea; the caller owns its value and validation.
// No wrapper is added, so existing field grids and layout classes keep working.
export default function FormField({ label, hint, error, children }) {
  const generatedId = useId();
  const control = Children.only(children);
  const id = control.props.id || generatedId;
  const describedBy = [control.props['aria-describedby'], hint && `${id}-hint`,
    error && `${id}-error`].filter(Boolean).join(' ') || undefined;
  return <>
    <label htmlFor={id}>{label}</label>
    {cloneElement(control, { id, 'aria-describedby': describedBy,
      'aria-invalid': error ? true : control.props['aria-invalid'] })}
    {hint && <p id={`${id}-hint`} className="panel-note">{hint}</p>}
    {error && <p id={`${id}-error`} role="alert">{error}</p>}
  </>;
}
