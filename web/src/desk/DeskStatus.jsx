// The strip along the bottom of the desk: what this computer is doing right
// now, in one line.
//
// Everything here is already on the desk somewhere — in a widget, in the rail,
// in Settings. The strip is for the things worth knowing without looking: how
// many apps are running, whether anything needs you, how much room is left, and
// whether this server is reachable from anywhere but this computer.
//
// It draws only what it actually has. A server that cannot report its disks
// shows no storage rather than a dash, and an empty strip does not render.
import { useCallback } from 'react';
import { api, formatBytes } from '../api.js';
import { automationsApi } from '../automationsApi.js';
import { useResource } from '../hooks/useResource.js';
import { useApps } from '../store.jsx';

// How this server can be reached, in the words the desk uses for it. `local`
// is the default and the quietest thing Vela can be.
const MODES = {
  local: 'Local',
  lan: 'LAN only',
  https: 'HTTPS',
};

export default function DeskStatus() {
  const { apps } = useApps();

  const loadMetrics = useCallback((options) => api.systemMetrics(options), []);
  const { data: metrics } = useResource(loadMetrics, { intervalMs: 30000 });

  const loadFlows = useCallback((options) => automationsApi.status(options), []);
  const { data: flows } = useResource(loadFlows, { intervalMs: 60000 });

  const loadSummaries = useCallback((options) => api.appWidgets(options), []);
  const { data: summaries } = useResource(loadSummaries, { intervalMs: 60000 });

  const running = (apps || []).filter((app) => app.installed && app.running).length;

  // The first app that says it needs attention, named. One line is what there
  // is room for, and the rest are in Needs you.
  const attention = (() => {
    const entry = (summaries?.widgets || []).find((item) => item?.summary?.attention);
    if (!entry) return '';
    const app = (apps || []).find((item) => item.id === entry.appId);
    const caption = entry.summary?.caption || entry.summary?.value || 'needs you';
    return `${app?.name || entry.appId} ${caption}`.trim();
  })();

  // The volume Vela's own data sits on, which is the one that filling up stops
  // Vela working.
  const disk = (metrics?.disks || []).find((entry) => entry.reachable && entry.total);
  const storage = disk ? `${formatBytes(disk.used)} of ${formatBytes(disk.total)}` : '';

  const network = metrics?.network;
  const today = network?.today?.total ? `${formatBytes(network.today.total)} today` : '';
  const mode = MODES[network?.mode] || '';

  const parts = [
    running ? `${running} ${running === 1 ? 'app' : 'apps'} running` : '',
    flows?.runsToday ? `${flows.runsToday} ${flows.runsToday === 1 ? 'flow' : 'flows'} today` : '',
    attention,
    storage,
    mode,
    today,
  ].filter(Boolean);

  if (!parts.length) return null;

  return (
    <div className="desk-statusbar" role="status" aria-label="This server">
      {parts.map((part, index) => (
        <span key={part + index} className="desk-statusbar-part">
          {part}
        </span>
      ))}
    </div>
  );
}
