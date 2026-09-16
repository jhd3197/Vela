// The pieces every desk widget draws with.
//
// Origin: ServerKit `frontend/src/components/dashboard/widgets/renderers.jsx`
// (MIT, same owner) — `WidgetChart`, `WidgetBoundary`, and the stat, meter,
// status, feed and clock bodies. ServerKit's renderers fetch their own data
// through that project's query client; here every piece takes what it draws as
// props, because the desk loads once in `DeskDataProvider` and hands the
// values down.
import { Component, useEffect, useId, useState } from 'react';
import { formatValue } from '../metrics.js';

/** A muted one-liner for "nothing here yet". */
export function DeskEmpty({ children }) {
  return <p className="desk-empty">{children}</p>;
}

/** The same line, phrased as a failure. */
export function DeskFailed({ subject = 'this' }) {
  return (
    <p className="desk-empty desk-empty-error" role="status">
      Could not load {subject}.
    </p>
  );
}

/** A skeleton line while the first response is in flight. */
export function DeskLoading({ label = 'Loading…' }) {
  return (
    <p className="desk-empty" role="status">
      {label}
    </p>
  );
}

/**
 * Sparkline. Hand-rolled SVG rather than a chart dependency: a desk widget
 * needs a line that fills its cell and nothing else. Gradient ids are
 * per-instance (`useId`) so two charts on one board cannot capture each
 * other's fills.
 */
export function WidgetChart({ series = [], color = 'var(--accent)', domain = null, height = 44 }) {
  const uid = useId().replace(/:/g, '');
  const values = series.filter((v) => typeof v === 'number' && Number.isFinite(v));
  if (values.length < 2) return null;

  const w = 100;
  const h = 100;
  // A fixed domain (percentages: [0, 100]) beats scaling to the data, which
  // both flattens a series under one spike and inflates a flat one into
  // meaningless mountains.
  const [dMin, dMax] = domain || [];
  const max = domain ? dMax : Math.max(...values) * 1.12;
  const min = domain ? dMin : Math.min(...values) * 0.7;
  const span = max - min || 1;
  const toX = (i) => (i / (values.length - 1 || 1)) * w;
  const toY = (v) => h - ((Math.max(min, Math.min(max, v)) - min) / span) * h;
  const line = values
    .map((v, i) => `${i ? 'L' : 'M'}${toX(i).toFixed(2)} ${toY(v).toFixed(2)}`)
    .join(' ');

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      className="desk-chart"
      style={{ height }}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={`desk-${uid}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.26" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${line} L ${w} ${h} L 0 ${h} Z`} fill={`url(#desk-${uid})`} />
      <path
        d={line}
        fill="none"
        stroke={color}
        strokeWidth="1.6"
        vectorEffect="non-scaling-stroke"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** One number with its unit, an optional delta and a caption under it. */
export function WidgetStat({ value, unit, delta, caption, color }) {
  return (
    <div className="desk-stat">
      <span className="desk-stat-value" style={color ? { color } : undefined}>
        {typeof value === 'number' ? formatValue(value, unit) : value || '—'}
        {typeof value !== 'number' && unit ? <span className="desk-stat-unit">{unit}</span> : null}
      </span>
      {delta ? <span className="desk-stat-delta">{delta}</span> : null}
      {caption ? <span className="desk-stat-caption">{caption}</span> : null}
    </div>
  );
}

/**
 * Linear meter. ServerKit's radial gauge is not copied: a desk widget is two
 * cells wide and one tall, and a bar reads better at that size.
 */
export function WidgetMeter({ percent, label, detail, color = 'var(--accent)' }) {
  const ratio = Math.max(0, Math.min(100, Number(percent) || 0));
  return (
    <div className="desk-meter">
      {(label || detail) && (
        <div className="desk-meter-head">
          {label ? <span className="desk-meter-label">{label}</span> : null}
          {detail ? <span className="desk-meter-detail">{detail}</span> : null}
        </div>
      )}
      <div
        className="desk-meter-track"
        role="progressbar"
        aria-valuenow={Math.round(ratio)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label || 'Progress'}
      >
        <span className="desk-meter-fill" style={{ width: `${ratio}%`, background: color }} />
      </div>
    </div>
  );
}

/** A row of live/idle cells: a dot, a name and one line of detail. */
export function WidgetStatus({ cells = [] }) {
  return (
    <ul className="desk-status">
      {cells.map((cell) => (
        <li className="desk-status-cell" key={cell.id}>
          {cell.lead || <span className="desk-status-dot" data-state={cell.state || 'ok'} />}
          <span className="desk-status-text">
            <span className="desk-status-name">{cell.name}</span>
            {cell.meta ? <span className="desk-status-meta">{cell.meta}</span> : null}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** A short list of label/detail rows. */
export function WidgetList({ rows = [] }) {
  return (
    <ul className="desk-list">
      {rows.map((row, index) => (
        <li className="desk-list-row" key={`${row.label}-${index}`}>
          <span className="desk-list-label">{row.label}</span>
          {row.detail ? <span className="desk-list-detail">{row.detail}</span> : null}
        </li>
      ))}
    </ul>
  );
}

/**
 * Wall clock. Ticks locally rather than polling: the time between two server
 * samples is not interesting, and a widget that re-fetches every second to
 * print a number the browser already knows would be silly.
 */
export function WidgetClock({ showSeconds = false, weather = null }) {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), showSeconds ? 1000 : 20000);
    return () => clearInterval(id);
  }, [showSeconds]);

  const time = now.toLocaleTimeString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
    ...(showSeconds ? { second: '2-digit' } : {}),
  });
  const date = now.toLocaleDateString(undefined, {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
  });

  // The weather line only exists when the user turned it on and the server had
  // something to say; there is no placeholder for a reading Vela does not have.
  const reading =
    weather?.enabled && typeof weather.temperature === 'number'
      ? [`${weather.temperature} ${weather.unit || '°C'}`, weather.description]
          .filter(Boolean)
          .join(' · ')
      : '';

  return (
    <div className="desk-clock">
      <span className="desk-clock-time">{time}</span>
      <span className="desk-clock-date">{date}</span>
      {reading && <span className="desk-clock-weather">{reading}</span>}
    </div>
  );
}

/**
 * Last line of defence. A renderer that throws — a widget fed a malformed
 * saved cfg, or an app summary shaped unexpectedly — degrades to a muted note
 * instead of unmounting the whole board.
 */
export class WidgetBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error) {
    console.error(`Desk widget "${this.props.widgetType}" crashed:`, error);
  }

  render() {
    if (this.state.failed) {
      return <p className="desk-empty desk-empty-error">This widget could not be shown.</p>;
    }
    return this.props.children;
  }
}
