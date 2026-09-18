// The pieces every desk widget draws with.
//
// These used to be the primitives. They are now the desk's names for the shared
// ones in `components/ds/`, which the Marketplace, Settings and an app's own
// published widget draw with too -- a widget an app publishes should be
// indistinguishable in chrome from one Vela drew itself, and that only stays
// true while there is one set of pieces.
//
// The wrappers are kept rather than replaced at every call site because the
// desk's vocabulary is its own: a widget asks for a `WidgetMeter` with a
// `percent` and a `color`, and the fact that a meter is tinted by a tone rather
// than by a colour is the design system's business, not the widget's.
//
// Origin of the shapes: ServerKit
// `frontend/src/components/dashboard/widgets/renderers.jsx` (MIT, same owner).
// ServerKit's renderers fetched their own data through that project's query
// client; here every piece takes what it draws as props, because the desk loads
// once in `DeskDataProvider` and hands the values down.
import { Component, useEffect, useState } from 'react';
import { KeyValue, Meter, Rows, Sparkline, Stat } from '../../components/ds/index.js';
import { formatValue } from '../metrics.js';

/** A muted one-liner for "nothing here yet". */
export function DeskEmpty({ children }) {
  return <p className="vela-empty">{children}</p>;
}

/** The same line, phrased as a failure. */
export function DeskFailed({ subject = 'this' }) {
  return (
    <p className="vela-empty vela-empty-error" role="status">
      Could not load {subject}.
    </p>
  );
}

/** A skeleton line while the first response is in flight. */
export function DeskLoading({ label = 'Loading…' }) {
  return (
    <p className="vela-empty" role="status">
      {label}
    </p>
  );
}

/** A line filling its cell. */
export function WidgetChart({ series = [], tone, domain = null, height = 44 }) {
  return <Sparkline series={series} domain={domain} height={height} tone={tone} />;
}

/** One number with its unit, an optional delta and a caption under it. */
export function WidgetStat({ value, unit, delta, caption, tone }) {
  const measured = typeof value === 'number';
  return (
    <Stat
      value={measured ? formatValue(value, unit) : value || null}
      unit={measured ? null : unit}
      delta={delta}
      deltaTone={tone}
      caption={caption}
    />
  );
}

/** Linear meter. */
export function WidgetMeter({ percent, label, detail, tone }) {
  return <Meter percent={percent} label={label} detail={detail} tone={tone} />;
}

/**
 * A row of live/idle cells: a dot, a name and one line of detail.
 *
 * The desk speaks in states (`ok`, `off`, `bad`); the primitive speaks in
 * tones. The translation lives here, in one place, so a widget that means
 * "this one failed" does not have to know that red is the colour for it.
 */
const TONE_FOR_STATE = { ok: 'green', off: 'neutral', bad: 'red', busy: 'cyan', waiting: 'amber' };

export function WidgetStatus({ cells = [] }) {
  return (
    <Rows
      rows={cells.map((cell) => ({
        id: cell.id,
        label: cell.name,
        detail: cell.meta,
        tail: cell.tail,
        lead: cell.lead,
        tone: TONE_FOR_STATE[cell.state] || 'green',
      }))}
    />
  );
}

/** A short list of label/detail rows. */
export function WidgetList({ rows = [] }) {
  return <KeyValue rows={rows.map((row) => ({ label: row.label, value: row.detail }))} />;
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
    <div className="vela-clock">
      <span className="vela-clock-time">{time}</span>
      <span className="vela-clock-date">{date}</span>
      {reading && <span className="vela-clock-weather">{reading}</span>}
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
      return <p className="vela-empty vela-empty-error">This widget could not be shown.</p>;
    }
    return this.props.children;
  }
}
