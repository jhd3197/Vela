/**
 * A labelled bar. `percent` is clamped rather than trusted: a widget fed a
 * malformed summary should draw a full bar, not a fill that runs off its track.
 */
export default function Meter({ percent, label, detail, tone, className = '', ...rest }) {
  const ratio = Math.max(0, Math.min(100, Number(percent) || 0));
  const classes = ['vela-meter', className].filter(Boolean).join(' ');
  return (
    <div className={classes} data-tone={tone} {...rest}>
      {(label || detail) && (
        <div className="vela-meter-head">
          {label ? <span className="vela-meter-label">{label}</span> : null}
          {detail ? <span className="vela-meter-detail">{detail}</span> : null}
        </div>
      )}
      <div
        className="vela-meter-track"
        role="progressbar"
        aria-valuenow={Math.round(ratio)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label || 'Progress'}
      >
        <span className="vela-meter-fill" style={{ width: `${ratio}%` }} />
      </div>
    </div>
  );
}
