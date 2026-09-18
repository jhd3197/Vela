// What is asking for you.
//
// Two sources, both of them things that actually said something: an app that
// published a summary with `attention: true`, and a health check that failed.
// Vela never decides on an app's behalf that something is wrong, so with
// nothing flagged this says everything is running rather than inventing a list
// of things to check.
import { Link } from 'react-router-dom';
import AppIcon from '../../components/AppIcon.jsx';
import Button from '../../components/ui/Button.jsx';
import { api } from '../../api.js';
import { useApps } from '../../store.jsx';
import useOpenApp from '../../desktops/useOpenApp.js';
import { useDeskData } from '../DeskDataProvider.jsx';
import { DeskEmpty, WidgetStatus } from './primitives.jsx';

export default function NeedsYouWidget() {
  const { apps, pushToast } = useApps();
  const openApp = useOpenApp();
  const { data, loaded, refresh } = useDeskData('appWidgets');
  const { data: health } = useDeskData('health');

  // Later puts one item aside for eight hours. It is a server-side setting, so
  // the list is asked again rather than the row hidden locally: what the desk
  // shows and what the rail's dot reads then cannot disagree.
  const later = async (entry) => {
    try {
      await api.snoozeWidget(entry.appId, entry.id);
      refresh();
      // The rail reads the same summaries on a poll of its own, so it is told
      // rather than left showing a dot for something already put aside.
      dispatchEvent(new Event('vela:widgets-changed'));
    } catch (error) {
      pushToast?.(error.message || 'Could not put that aside.', 'error');
    }
  };

  // A snoozed item is still published and still shown by the app's own widget;
  // it is only this list and the rail's dot that leave it alone until it is due.
  const flagged = (data?.widgets || []).filter(
    (entry) => entry.summary?.attention && !entry.snoozedUntil,
  );
  // One row per app, not per widget: two flagged widgets of the same app are
  // one thing needing attention.
  const seen = new Set();
  const rows = [];
  for (const entry of flagged) {
    if (seen.has(entry.appId)) continue;
    seen.add(entry.appId);
    const app = (apps || []).find((item) => item.id === entry.appId);
    const name = entry.appName || app?.name || entry.appId;
    // Only actions the user has already allowed this app to ask for. As in the
    // app's own widget, the button opens the app: Vela does not run an app's
    // action on its behalf from the desk.
    const granted = new Set(entry.grantedActions || []);
    const offered = (entry.summary?.actions || []).filter((action) => granted.has(action.action));
    rows.push({
      id: entry.appId,
      name,
      meta: entry.summary?.caption || entry.name,
      state: 'bad',
      lead: app ? (
        <button
          type="button"
          className="desk-status-open"
          aria-label={`Open ${name}`}
          onClick={() => openApp(app.id, { returnTo: '/' })}
        >
          <AppIcon app={app} size={24} />
        </button>
      ) : null,
      tail: (
        <>
          {offered.map((action) => (
            <Button
              key={action.action}
              size="small"
              onClick={() => app && openApp(app.id, { returnTo: '/' })}
            >
              {action.label}
            </Button>
          ))}
          <Button size="small" variant="ghost" onClick={() => later(entry)}>
            Later
          </Button>
        </>
      ),
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

  // A new release is Vela asking for attention too, once.
  if (health?.update?.available) {
    rows.push({
      id: 'update',
      name: `Vela ${health.update.latest} is available`,
      meta: `You are running ${health.update.current}`,
      state: 'warn',
      lead: (
        <Link className="desk-status-open" to="/settings#updates" aria-label="Open Updates">
          <span className="desk-status-dot" data-state="warn" />
        </Link>
      ),
    });
  }

  if (!loaded && !data) return <DeskEmpty>Checking…</DeskEmpty>;
  if (rows.length === 0) return <DeskEmpty>Everything’s running.</DeskEmpty>;
  return <WidgetStatus cells={rows} />;
}
