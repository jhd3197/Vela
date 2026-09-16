// What is running right now.
//
// The source is the same `/api/apps` list the rail reads: an app is running
// when the engine says so. Nothing is inferred from a recent visit.
import AppIcon from '../../components/AppIcon.jsx';
import { useApps } from '../../store.jsx';
import { DeskEmpty, WidgetStatus } from './primitives.jsx';

export default function RunningWidget() {
  const { apps, openApp } = useApps();
  const running = (apps || [])
    .filter((app) => app.installed && app.running)
    .sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));

  if (apps === null) return <DeskEmpty>Checking what’s running…</DeskEmpty>;
  if (running.length === 0) return <DeskEmpty>Nothing running.</DeskEmpty>;

  return (
    <WidgetStatus
      cells={running.map((app) => ({
        id: app.id,
        name: app.name,
        meta: 'Running',
        state: 'ok',
        lead: (
          <button
            type="button"
            className="desk-status-open"
            aria-label={`Open ${app.name}`}
            onClick={() => openApp(app.id, { returnTo: '/' })}
          >
            <AppIcon app={app} size={24} />
          </button>
        ),
      }))}
    />
  );
}
