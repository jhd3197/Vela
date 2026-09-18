/**
 * A proportion drawn as a ring, with its number in the middle.
 *
 * For the case a meter cannot carry: "eleven of twelve checks pass" should read
 * as almost-whole at a glance, and a bar 96 % along reads as a bar.
 *
 * The circle is stroked with a dash the length of its own circumference and
 * offset by the part that is missing, which is how an SVG draws an arc without
 * any path arithmetic.
 */
export default function Ring({
  percent,
  value,
  caption,
  tone,
  size = 76,
  thickness = 7,
  label,
  className = '',
  ...rest
}) {
  const ratio = Math.max(0, Math.min(100, Number(percent) || 0));
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;
  const classes = ['vela-ring', className].filter(Boolean).join(' ');
  return (
    <div
      className={classes}
      data-tone={tone}
      role="img"
      aria-label={label || `${Math.round(ratio)} per cent`}
      {...rest}
    >
      <svg
        className="vela-ring-figure"
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        aria-hidden="true"
      >
        <circle
          className="vela-ring-track"
          cx={size / 2}
          cy={size / 2}
          r={radius}
          strokeWidth={thickness}
        />
        <circle
          className="vela-ring-fill"
          cx={size / 2}
          cy={size / 2}
          r={radius}
          strokeWidth={thickness}
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - ratio / 100)}
        />
      </svg>
      <span className="vela-ring-label">
        <span className="vela-ring-value">{value ?? `${Math.round(ratio)}%`}</span>
        {caption ? <span className="vela-ring-caption">{caption}</span> : null}
      </span>
    </div>
  );
}
