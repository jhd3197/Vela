/**
 * A short series as vertical bars, in three accent steps.
 *
 * The prototype's rule: ordinary bars at 300, anything above the median at 400,
 * the last bar at 600. That is what lets the chart be read without an axis or a
 * legend -- the eye finds the newest bar first and the tall ones second.
 *
 * A fixed `domain` beats scaling to the data, which both flattens a series
 * under one spike and inflates a flat one into meaningless mountains. Without
 * one the series is scaled to its own maximum, with a floor so a run of zeroes
 * is still drawn as a row of marks rather than as nothing.
 */
export default function Bars({ series = [], domain = null, caption, className = '', ...rest }) {
  const values = series.filter((value) => typeof value === 'number' && Number.isFinite(value));
  if (values.length === 0) return null;

  const [low, high] = domain || [0, Math.max(...values, 1)];
  const span = high - low || 1;
  const sorted = [...values].sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];
  const classes = ['vela-bars', className].filter(Boolean).join(' ');

  return (
    <div className={classes} {...rest}>
      <div className="vela-bars-plot">
        {values.map((value, index) => {
          const share = Math.max(0, Math.min(1, (value - low) / span));
          const weight = index === values.length - 1 ? 'last' : value > median ? 'high' : undefined;
          return (
            <span
              // A series is positional: the third bar is the third day whatever
              // its value, so the index is the identity here.
              key={index}
              className="vela-bar"
              data-weight={weight}
              // The one inline style in the layer: a bar's height is data, and
              // a stylesheet cannot hold a number that changes every minute.
              style={{ height: `${Math.max(share * 100, 4)}%` }}
            />
          );
        })}
      </div>
      {caption ? <span className="vela-bars-caption">{caption}</span> : null}
    </div>
  );
}
