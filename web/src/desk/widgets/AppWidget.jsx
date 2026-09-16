// A widget an app provides.
//
// The app publishes a small JSON summary over the bridge; this renders it with
// host components. No app code runs on the desk, nothing here interprets
// markup, and the owning app is always named — a line on the user's home
// screen should never be anonymous.
import AppIcon from '../../components/AppIcon.jsx';
import Button from '../../components/ui/Button.jsx';
import { useApps } from '../../store.jsx';
import { useDeskData } from '../DeskDataProvider.jsx';
import { formatRelativeTime } from '../metrics.js';
import { DeskEmpty, WidgetList, WidgetMeter, WidgetStat } from './primitives.jsx';

// A summary older than this reads as "as of …" rather than as the current
// state, even without an `expiresAt`.
const STALE_MS = 60 * 60 * 1000;

function isStale(record) {
  if (!record?.updatedAt) return false;
  const expires = record.summary?.expiresAt ? Date.parse(record.summary.expiresAt) : null;
  if (expires && Date.now() > expires) return true;
  const updated = Date.parse(record.updatedAt);
  return Number.isFinite(updated) && Date.now() - updated > STALE_MS;
}

function Body({ layout, summary }) {
  if (layout === 'progress') {
    return (
      <>
        <WidgetMeter
          percent={summary.progress ?? 0}
          label={summary.value || ''}
          detail={summary.unit || ''}
        />
        {summary.caption ? <p className="desk-stat-caption">{summary.caption}</p> : null}
      </>
    );
  }
  if (layout === 'list') {
    return summary.rows?.length ? (
      <WidgetList rows={summary.rows} />
    ) : (
      <DeskEmpty>{summary.caption || 'Nothing to show.'}</DeskEmpty>
    );
  }
  // `stat` and `actions` share the same headline; `actions` adds its buttons
  // below, which the frame renders separately.
  return (
    <WidgetStat
      value={summary.value}
      unit={summary.unit}
      delta={summary.delta}
      caption={summary.caption}
    />
  );
}

export default function AppWidget({ type }) {
  const { openApp } = useApps();
  const { data } = useDeskData('appWidgets');
  const app = type?.app;
  const record = (data?.widgets || []).find(
    (entry) => entry.appId === app?.id && entry.id === type?.widgetId,
  );
  const summary = record?.summary;

  if (!app) return <DeskEmpty>This widget is no longer available.</DeskEmpty>;

  const open = () => openApp(app.id, { returnTo: '/' });

  // An action is offered only when the user has actually granted it to this
  // app; an ungranted one is not drawn at all, rather than shown as a control
  // that cannot mean anything. Choosing one opens the app at it: the engine has
  // no host-initiated action path, and the desk does not act for the user.
  const granted = new Set(record?.grantedActions || []);
  const offered = (summary?.actions || []).filter((action) => granted.has(action.action));

  return (
    <>
      <div className="desk-app-head">
        <button
          type="button"
          className="desk-app-open"
          aria-label={`Open ${app.name}`}
          onClick={open}
        >
          <AppIcon app={app} size={24} />
        </button>
        <span className="desk-app-name">{app.name}</span>
        <span className="desk-app-widget">{type.name}</span>
        {summary?.attention ? (
          <span className="desk-app-attention" title="Needs you">
            <span className="desk-status-dot" data-state="bad" />
          </span>
        ) : null}
      </div>

      {summary ? (
        <Body layout={type.layout} summary={summary} />
      ) : (
        <DeskEmpty>Open {app.name} to update this.</DeskEmpty>
      )}

      {summary && isStale(record) ? (
        <p className="desk-stat-caption">as of {formatRelativeTime(record.updatedAt)}</p>
      ) : null}

      {type.layout === 'actions' && offered.length ? (
        <div className="desk-app-actions">
          {offered.map((action) => (
            <Button key={action.action} size="small" onClick={open}>
              {action.label}
            </Button>
          ))}
        </div>
      ) : null}
    </>
  );
}
