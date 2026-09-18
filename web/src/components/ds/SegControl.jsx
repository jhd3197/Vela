import { useId } from 'react';

/**
 * A segmented control on native radios.
 *
 * Origin: ServerKit `frontend/src/components/ds/SegControl.jsx` (MIT, same
 * owner), which drew buttons and wired arrow keys by hand.
 *
 * A radio group already does all of that: arrow keys move the selection, Home
 * and End reach the ends, each option is announced with its position, and the
 * selection posts inside a form. The name is per-instance so two controls on
 * one screen cannot capture each other's selection.
 */
export default function SegControl({
  options = [],
  value,
  onChange,
  name,
  label,
  disabled = false,
  className = '',
  ...rest
}) {
  const generated = useId();
  const group = name || `vela-seg-${generated.replace(/:/g, '')}`;
  const classes = ['vela-seg', className].filter(Boolean).join(' ');
  return (
    <div className={classes} role="radiogroup" aria-label={label} {...rest}>
      {options.map((option) => (
        <label className="vela-seg-opt" key={option.value}>
          <input
            className="vela-seg-input"
            type="radio"
            name={group}
            value={option.value}
            checked={option.value === value}
            disabled={disabled || option.disabled}
            onChange={() => onChange?.(option.value)}
          />
          <span className="vela-seg-label">{option.label}</span>
        </label>
      ))}
    </div>
  );
}
