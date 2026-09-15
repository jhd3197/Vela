// Pure number helpers for desk widgets.
//
// Origin: ServerKit `frontend/src/components/dashboard/widgets/metrics.js`
// (MIT, same owner). Only the side-effect-free functions are copied; the
// ServerKit metric catalog, which names columns on that project's history
// tables, is not — Vela widgets are handed the values they draw.

/** Reduce a series to one number. Unknown aggregations behave as 'last'. */
export function aggregate(series, agg) {
  const values = (series || []).filter((v) => typeof v === 'number' && Number.isFinite(v));
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  switch (agg) {
    case 'avg':
      return values.reduce((a, b) => a + b, 0) / values.length;
    case 'max':
      return sorted[sorted.length - 1];
    case 'min':
      return sorted[0];
    case 'p95':
      return sorted[Math.max(0, Math.ceil(sorted.length * 0.95) - 1)];
    default:
      return values[values.length - 1];
  }
}

/**
 * Percentage change of the aggregate between the older 60% of the window and
 * the whole window. Returns null when there is not enough history to be honest
 * about it.
 */
export function deltaPercent(series, agg) {
  const values = (series || []).filter((v) => typeof v === 'number' && Number.isFinite(v));
  if (values.length < 4) return null;
  const head = values.slice(0, Math.floor(values.length * 0.6));
  if (!head.length) return null;
  const now = aggregate(values, agg);
  const before = aggregate(head, agg);
  if (now === null || !before) return null;
  return ((now - before) / Math.abs(before)) * 100;
}

/** Human-readable value + unit. `null`/NaN render as an em dash. */
export function formatValue(value, unit) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  if (unit === '%') return `${Math.round(value * 10) / 10}%`;
  const num =
    Math.abs(value) >= 1000 ? `${(value / 1000).toFixed(1)}k` : `${Math.round(value * 100) / 100}`;
  return unit ? `${num} ${unit}` : num;
}

/**
 * Threshold colour for a value. `thresholds` is [amber, red]; anything below
 * amber is green. Returns null when no thresholds are configured so callers
 * can fall back to their own colour.
 */
export function thresholdColor(value, thresholds) {
  if (!Array.isArray(thresholds) || thresholds.length === 0) return null;
  if (value === null || value === undefined || !Number.isFinite(value)) return null;
  const [amber, red] = thresholds;
  if (Number.isFinite(red) && value >= red) return 'var(--red)';
  if (Number.isFinite(amber) && value >= amber) return 'var(--accent)';
  return 'var(--green)';
}

/**
 * Fixed y-axis domain for a chart, or null to scale to the data.
 *
 * Percentages get a hard [0, 100]. Auto-scaling them reads badly in both
 * directions: one spike compresses the rest into a flat line, and a series
 * that idles between 0.4% and 0.7% is stretched into dramatic mountains that
 * mean nothing. A percentage already has an absolute frame of reference.
 */
export function chartDomain(units) {
  const list = (units || []).filter((unit) => unit !== undefined && unit !== null);
  if (!list.length) return null;
  return list.every((unit) => unit === '%') ? [0, 100] : null;
}

const SIZES = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];

/** Bytes as the largest unit that keeps the number readable. */
export function formatBytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value < 0) return '—';
  let n = value;
  let i = 0;
  while (n >= 1024 && i < SIZES.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n >= 100 || i === 0 ? Math.round(n) : Math.round(n * 10) / 10} ${SIZES[i]}`;
}

/** "3 minutes ago" / "in 2 hours", or an empty string for an unusable stamp. */
export function formatRelativeTime(value) {
  const then = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(then.getTime())) return '';
  const seconds = Math.round((then.getTime() - Date.now()) / 1000);
  const units = [
    ['second', 60],
    ['minute', 60],
    ['hour', 24],
    ['day', 7],
    ['week', 4.35],
    ['month', 12],
    ['year', Infinity],
  ];
  const format = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  let amount = seconds;
  for (const [unit, step] of units) {
    if (Math.abs(amount) < step) return format.format(Math.round(amount), unit);
    amount /= step;
  }
  return format.format(Math.round(amount), 'year');
}

/** "41 days", "6 hours", "12 minutes" — the uptime line's tail. */
export function formatDuration(seconds) {
  const total = Number(seconds);
  if (!Number.isFinite(total) || total < 0) return '—';
  const days = Math.floor(total / 86400);
  if (days >= 1) return `${days} day${days === 1 ? '' : 's'}`;
  const hours = Math.floor(total / 3600);
  if (hours >= 1) return `${hours} hour${hours === 1 ? '' : 's'}`;
  const minutes = Math.floor(total / 60);
  if (minutes >= 1) return `${minutes} minute${minutes === 1 ? '' : 's'}`;
  return 'less than a minute';
}
