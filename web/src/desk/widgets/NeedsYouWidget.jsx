// What is asking for you.
//
// The only source is the apps themselves: a published summary with
// `attention: true`. Vela never decides on an app's behalf that something is
// wrong, so with nothing flagged this says everything is running rather than
// inventing a list of things to check.
import AppIcon from '../../components/AppIcon.jsx';
import { useApps } from '../../store.jsx';
import { useDeskData } from '../DeskDataProvider.jsx';
import { DeskEmpty, WidgetStatus } from './primitives.jsx';

export default function NeedsYouWidget() {
  const { apps, openApp } = useApps();
  const { data, loaded } = useDeskData('appWidgets');

  const flagged = (data?.widgets || []).filter((entry) => entry.summary?.attention);
  // One row per app, not per widget: two flagged widgets of the same app are
  // one thing needing attention.
  const seen = new Set();
  const rows = [];
  for (const entry of flagged) {
    if (seen.has(entry.appId)) continue;
    seen.add(entry.appId);
    const app = (apps || []).find((item) => item.id === entry.appId);
    rows.push({
      id: entry.appId,
      name: entry.appName || app?.name || entry.appId,
      meta: entry.summary?.caption || entry.name,
      state: 'bad',
      lead: app ? (
        <button
          type="button"
          className="desk-status-open"
          aria-label={`Open ${app.name}`}
          onClick={() => openApp(app.id, { returnTo: '/' })}
        >
          <AppIcon app={app} size={24} />
        </button>
      ) : null,
    });
  }

  if (!loaded && !data) return <DeskEmpty>Checking…</DeskEmpty>;
  if (rows.length === 0) return <DeskEmpty>Everything’s running.</DeskEmpty>;
  return <WidgetStatus cells={rows} />;
}
