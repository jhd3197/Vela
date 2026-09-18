/**
 * A dot and a word, tinted by what it means.
 *
 * Origin: ServerKit `frontend/src/components/ds/Pill.jsx` (MIT, same owner).
 * The tone is a data attribute rather than a lookup in JavaScript, so the
 * stylesheet owns the colour and nothing has to import a palette.
 */
export default function Pill({ tone, dot = true, className = '', children, ...rest }) {
  const classes = ['vela-pill', className].filter(Boolean).join(' ');
  return (
    <span className={classes} data-tone={tone} {...rest}>
      {dot ? <span className="vela-pill-dot" aria-hidden="true" /> : null}
      {children}
    </span>
  );
}
