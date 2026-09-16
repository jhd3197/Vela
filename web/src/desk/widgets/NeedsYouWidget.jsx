// What is asking for you.
//
// Two sources, both of them things that actually said something: an app that
// published a summary with `attention: true`, and a health check that failed.
// Vela never decides on an app's behalf that something is wrong, so with
// nothing flagged this says everything is running rather than inventing a list
// of things to check.
import { Link } from 'react-router-dom';
import AppIcon from '../../components/AppIcon.jsx';
import { useApps } from '../../store.jsx';
import { useDeskData } from '../DeskDataProvider.jsx';
import { DeskEmpty, WidgetStatus } from './primitives.jsx';

export default function NeedsYouWidget() {
  const { apps, openApp } = useApps();
  const { data, loaded } = useDeskData('appWidgets');
  const { data: health } = useDeskData('health');

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

  // A failed check is Vela itself asking for attention, so it belongs in the
  // same list as an app that asked.
  for (const check of (health?.checks || []).filter((entry) => entry.status === 'fail')) {
    rows.push({
      id: `check:${check.key}`,
      name: check.title,
      meta: check.detail,
      state: 'bad',
      lead: (
        <Link className="desk-status-open" to="/settings#health" aria-label="Open Health settings">
          <span className="desk-status-dot" data-state="bad" />
        </Link>
      ),
    });
  }

  if (!loaded && !data) return <DeskEmpty>Checking…</DeskEmpty>;
  if (rows.length === 0) return <DeskEmpty>Everything’s running.</DeskEmpty>;
  return <WidgetStatus cells={rows} />;
}
