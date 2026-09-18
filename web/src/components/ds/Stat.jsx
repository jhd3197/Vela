/**
 * One number with its unit, an optional signed delta and a caption.
 *
 * The delta's tone is the widget's to decide, not the sign's: "storage used, up
 * 4 %" is not good news, and a component that turns every rise green would say
 * it was.
 */
export default function Stat({ value, unit, delta, deltaTone, caption, className = '', ...rest }) {
  const classes = ['vela-stat', className].filter(Boolean).join(' ');
  const empty = value === null || value === undefined || value === '';
  return (
    <div className={classes} {...rest}>
      <span className="vela-stat-value">
        {empty ? '—' : value}
        {!empty && unit ? <span className="vela-stat-unit">{unit}</span> : null}
      </span>
      {delta ? (
        <span className="vela-stat-delta" data-tone={deltaTone}>
          {delta}
        </span>
      ) : null}
      {caption ? <span className="vela-stat-caption">{caption}</span> : null}
    </div>
  );
}
