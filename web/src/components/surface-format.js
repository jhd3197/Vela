// How a surface value becomes text.
//
// The contract (vela-contracts docs/SURFACES.md): producers send raw values
// and a `format`; the host formats for the viewer's locale. Vela's dashboard
// is English-first, so this is `Intl` with the browser's locale, sharing the
// desk's byte and duration ladders rather than growing a second set.
import { formatBytes, formatDuration } from '../desk/metrics.js';

/** A percent, 0-100: one decimal under ten, whole numbers above. */
export function formatPercent(value) {
  const decimals = Math.abs(value) < 10 ? 1 : 0;
  return `${new Intl.NumberFormat(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value)}%`;
}

/** A moment, from an ISO string or epoch milliseconds. */
export function formatSurfaceTime(value) {
  const moment = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(moment.getTime())) return '—';
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(
    moment,
  );
}

/**
 * The one entry point every value node uses. A value with no applicable
 * format is text; a missing one is an em dash, exactly as the desk draws it.
 */
export function formatSurfaceValue(value, format) {
  if (value === null || value === undefined || value === '') return '—';
  if (format === 'time') return formatSurfaceTime(value);
  if (typeof value !== 'number' || !Number.isFinite(value)) return String(value);
  switch (format) {
    case 'percent':
      return formatPercent(value);
    case 'bytes':
      return formatBytes(value);
    case 'duration':
      return formatDuration(value);
    case 'text':
      return String(value);
    default:
      return new Intl.NumberFormat().format(value);
  }
}
