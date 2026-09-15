import { useEffect, useId, useRef, useState } from 'react';
import { Backspace } from '@phosphor-icons/react';

export const PIN_LENGTH = 6;

const digits = (value) => value.replace(/\D/g, '').slice(0, PIN_LENGTH);

// Six masked indicators and a familiar keypad. A physical keyboard types into
// it directly, and anyone who would rather use a labelled field can switch to
// one — but never both at once, so a phone shows a single keypad.
export default function PinPad({
  value,
  onChange,
  onComplete,
  disabled = false,
  label = 'PIN',
  autoFocus = false,
}) {
  const id = useId();
  const [field, setField] = useState(false);
  const fieldRef = useRef(null);
  const firstKey = useRef(null);
  const completeRef = useRef(onComplete);
  completeRef.current = onComplete;

  useEffect(() => {
    if (!autoFocus) return;
    if (field) fieldRef.current?.focus();
    else firstKey.current?.focus({ preventScroll: true });
  }, [autoFocus, field]);

  // The keypad is a set of buttons, so a physical keyboard needs its own path.
  // A focused text entry keeps its own keystrokes.
  useEffect(() => {
    if (field || disabled) return undefined;
    const onKey = (event) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target;
      if (target instanceof HTMLElement && target.closest('input, textarea, [contenteditable]'))
        return;
      if (event.key >= '0' && event.key <= '9') {
        event.preventDefault();
        onChange(digits(value + event.key));
      } else if (event.key === 'Backspace') {
        event.preventDefault();
        onChange(value.slice(0, -1));
      } else if (event.key === 'Enter' && value.length === PIN_LENGTH) {
        event.preventDefault();
        completeRef.current?.(value);
      }
    };
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  }, [field, disabled, value, onChange]);

  const press = (key) => {
    if (disabled) return;
    const next = key === 'delete' ? value.slice(0, -1) : digits(value + key);
    onChange(next);
    if (next.length === PIN_LENGTH) completeRef.current?.(next);
  };

  return (
    <div className="pinpad">
      <div
        className="pin-dots"
        role="img"
        aria-label={`${label}: ${value.length} of ${PIN_LENGTH} digits entered`}
      >
        {Array.from({ length: PIN_LENGTH }, (_, index) => (
          <span key={index} className={`pin-dot${index < value.length ? ' is-filled' : ''}`} />
        ))}
      </div>

      {field ? (
        <div className="pin-field">
          <label htmlFor={`${id}-pin`}>{label}</label>
          <input
            id={`${id}-pin`}
            ref={fieldRef}
            type="password"
            inputMode="numeric"
            autoComplete="off"
            pattern="[0-9]*"
            maxLength={PIN_LENGTH}
            disabled={disabled}
            value={value}
            onChange={(event) => onChange(digits(event.target.value))}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && value.length === PIN_LENGTH) {
                event.preventDefault();
                completeRef.current?.(value);
              }
            }}
          />
        </div>
      ) : (
        <div className="pin-keys" role="group" aria-label={`${label} keypad`}>
          {['1', '2', '3', '4', '5', '6', '7', '8', '9'].map((key, index) => (
            <button
              key={key}
              ref={index === 0 ? firstKey : null}
              type="button"
              className="pin-key"
              disabled={disabled}
              onClick={() => press(key)}
            >
              {key}
            </button>
          ))}
          <span className="pin-key pin-key-empty" aria-hidden="true" />
          <button type="button" className="pin-key" disabled={disabled} onClick={() => press('0')}>
            0
          </button>
          <button
            type="button"
            className="pin-key pin-key-delete"
            aria-label="Delete last digit"
            disabled={disabled || value.length === 0}
            onClick={() => press('delete')}
          >
            <Backspace size={22} aria-hidden="true" />
          </button>
        </div>
      )}

      <button
        type="button"
        className="link-btn pin-swap"
        onClick={() => {
          setField((on) => !on);
          onChange('');
        }}
      >
        {field ? 'Use the keypad' : 'Type it in a field instead'}
      </button>
    </div>
  );
}
