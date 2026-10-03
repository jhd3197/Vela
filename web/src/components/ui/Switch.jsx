import { forwardRef } from 'react';

// An on/off choice that takes effect at once. Any yes/no setting uses this,
// never a pair of On/Off buttons or a bare checkbox, so every boolean in the
// dashboard looks and answers the same way. Name it with `aria-label` or
// `aria-labelledby`; a setting row passes its title's id.
const Switch = forwardRef(function Switch(
  { checked, onChange, disabled = false, className = '', ...props },
  ref,
) {
  return (
    <button
      {...props}
      ref={ref}
      type="button"
      role="switch"
      aria-checked={Boolean(checked)}
      disabled={disabled}
      className={`switch${checked ? ' switch-on' : ''}${className ? ` ${className}` : ''}`}
      onClick={() => onChange?.(!checked)}
    />
  );
});

export default Switch;
