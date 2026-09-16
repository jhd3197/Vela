import { useCallback } from 'react';
import { api } from '../api.js';
import { useResource } from '../hooks/useResource.js';

// How often the desk asks the server for a reading. The server caches for
// fifteen minutes and makes no outbound request while the switch is off, so
// this interval decides how soon a change of place shows up rather than how
// often anything leaves the computer.
const REFRESH_MS = 5 * 60 * 1000;

// The desk's weather line, or null when it is switched off. Reading it through
// the server rather than from the browser is deliberate: the request is made
// once for the machine, not once per open tab, and the coordinates never reach
// the page.
export default function useWeather(enabled) {
  const load = useCallback((options) => api.weather(options), []);
  const { data } = useResource(load, { enabled: Boolean(enabled), intervalMs: REFRESH_MS });
  return data?.enabled ? data : null;
}
