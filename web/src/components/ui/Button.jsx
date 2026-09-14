import { forwardRef } from 'react';

// Navigation remains a Link. This control always has native button semantics.
const Button = forwardRef(function Button(
  {
    variant,
    size,
    block = false,
    pending = false,
    disabled = false,
    type = 'button',
    className = '',
    children,
    ...props
  },
  ref,
) {
  const classes = [
    'btn',
    variant && `btn-${variant}`,
    size && `btn-${size}`,
    block && 'btn-block',
    className,
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <button
      {...props}
      ref={ref}
      type={type}
      className={classes}
      disabled={disabled || pending}
      aria-busy={pending || undefined}
    >
      {children}
    </button>
  );
});

export default Button;
