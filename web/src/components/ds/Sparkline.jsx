import { useId } from 'react';

/**
 * A line with a wash under it, filling its cell.
 *
 * Hand-rolled SVG rather than a chart dependency: a desk widget needs a line
 * that fits and nothing else. Where `Bars` is for a series short enough to
 * count, this is for one too long to.
 *
 * Gradient ids are per-instance, so two sparklines on one board cannot capture
 * each other's fill.
 */
export default function Sparkline({
  series = [],
  domain = null,
  height = 44,
  tone,
  label,
  className = '',
  ...rest
}) {
  const uid = useId().replace(/:/g, '');
  const values = series.filter((value) => typeof value === 'number' && Number.isFinite(value));
  if (values.length < 2) return null;

  const width = 100;
  const box = 100;
  // A fixed domain (percentages: [0, 100]) beats scaling to the data, which
  // both flattens a series under one spike and inflates a flat one into
  // meaningless mountains.
  const [low, high] = domain || [Math.min(...values) * 0.7, Math.max(...values) * 1.12];
  const span = high - low || 1;
  const toX = (index) => (index / (values.length - 1 || 1)) * width;
  const toY = (value) => box - ((Math.max(low, Math.min(high, value)) - low) / span) * box;
  const line = values
    .map((value, index) => `${index ? 'L' : 'M'}${toX(index).toFixed(2)} ${toY(value).toFixed(2)}`)
    .join(' ');

  return (
    <svg
      viewBox={`0 0 ${width} ${box}`}
      preserveAspectRatio="none"
      className={['vela-sparkline', className].filter(Boolean).join(' ')}
      data-tone={tone}
      // The height is the cell's, not the viewBox's: the line stretches.
      style={{ height }}
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : 'true'}
      {...rest}
    >
      <defs>
        <linearGradient id={`vela-spark-${uid}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--tone, var(--accent))" stopOpacity="0.26" />
          <stop offset="100%" stopColor="var(--tone, var(--accent))" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${line} L ${width} ${box} L 0 ${box} Z`} fill={`url(#vela-spark-${uid})`} />
      <path
        d={line}
        fill="none"
        stroke="var(--tone, var(--accent))"
        strokeWidth="1.6"
        vectorEffect="non-scaling-stroke"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}
