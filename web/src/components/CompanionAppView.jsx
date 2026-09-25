import { useCallback, useState } from 'react';
import { ArrowClockwise, Play } from '@phosphor-icons/react';
import { api, companionState } from '../api.js';
import { useApps } from '../store.jsx';
import { useResource } from '../hooks/useResource.js';
import useCompanionAction from '../desk/useCompanionAction.js';
import { WidgetBody } from '../desk/widgets/AppWidget.jsx';
import { DeskEmpty } from '../desk/widgets/primitives.jsx';
import { formatRelativeTime } from '../desk/metrics.js';
import { Card } from './ds/index.js';
import Button from './ui/Button.jsx';
import AppIcon from './AppIcon.jsx';
import Shell from './Shell.jsx';
import WorkspacePage from './WorkspacePage.jsx';

// A companion app: a program with its own window on the Vela computer.
//
// Vela cannot show that window, and does not pretend to. What it can show is
// what the program offers to travel — its widgets and its buttons — which is
// the part someone on a phone wants anyway.
export default function CompanionAppView({ app, onStatus }) {
  const { pushToast } = useApps();
  const [busy, setBusy] = useState(false);
  const state = companionState(app);
  const companion = app.companion || {};
  const load = useCallback(() => api.companionWidgets(app.id), [app.id]);
  const widgets = useResource(load, { intervalMs: 10000 });
  const { run, running } = useCompanionAction(widgets.refresh);

  const act = async (work, done) => {
    setBusy(true);
    try {
      await work();
      onStatus?.();
      widgets.refresh();
      if (done) pushToast?.(done);
    } catch (error) {
      pushToast?.(error.message, 'error');
    } finally {
      setBusy(false);
    }
  };

  const online = companion.state === 'online';
  const pending = companion.pending;

  return (
    <Shell>
      <WorkspacePage
        title={app.name}
        subtitle="On this computer"
        lead={<AppIcon app={app} size={26} />}
        actions={
          <>
            <span className={`badge badge-managed badge-${state.tone}`}>{state.label}</span>
            <button
              className="btn btn-small btn-icon"
              onClick={() => act(() => api.refreshCompanion(app.id))}
              disabled={busy || !online}
              aria-label={`Refresh ${app.name}`}
            >
              <ArrowClockwise size={16} aria-hidden="true" />
            </button>
          </>
        }
      >
        <div className="companion-view">
          {companion.state === 'changed' && pending ? (
            <Card title="Review changes" tone="amber">
              <p className="companion-note">{state.detail}</p>
              <CompanionFacts entry={pending} />
              <div className="actions">
                <Button
                  variant="primary"
                  disabled={busy}
                  onClick={() =>
                    act(() => api.reviewCompanion(app.id, pending.fingerprint), 'Changes accepted')
                  }
                >
                  Accept changes
                </Button>
              </div>
            </Card>
          ) : null}

          {companion.state === 'offline' ? (
            <Card title="Not running">
              <p className="companion-note">{state.detail}</p>
              {companion.canStart ? (
                <div className="actions">
                  <Button
                    variant="primary"
                    disabled={busy}
                    onClick={() => act(() => api.launch(app.id), `Starting ${app.name}`)}
                  >
                    <Play size={16} aria-hidden="true" /> Start on this computer
                  </Button>
                </div>
              ) : null}
            </Card>
          ) : null}

          {online && state.detail ? <p className="companion-note">{state.detail}</p> : null}

          {(companion.actions || []).length ? (
            <section className="companion-actions" aria-label={`${app.name} actions`}>
              {companion.actions.map((action) => (
                <Button
                  key={action.id}
                  onClick={() => run(app, action.id)}
                  disabled={!online || running === action.id}
                  title={action.description || undefined}
                >
                  {action.title}
                </Button>
              ))}
            </section>
          ) : null}

          <div className="companion-widgets">
            {(widgets.data?.widgets || []).map((record) => (
              <Card
                key={record.id}
                title={record.name}
                tone={record.summary?.attention ? 'amber' : 'accent'}
                meta={
                  record.updatedAt ? (
                    <span className="desk-app-name">{formatRelativeTime(record.updatedAt)}</span>
                  ) : null
                }
                footer={
                  record.layout === 'actions' && record.summary?.actions?.length
                    ? record.summary.actions.map((action) => (
                        <Button
                          key={action.action}
                          size="small"
                          onClick={() => run(app, action.action)}
                          disabled={!online || running === action.action}
                        >
                          {action.label}
                        </Button>
                      ))
                    : null
                }
              >
                {record.summary ? (
                  <WidgetBody layout={record.layout} summary={record.summary} size={record.size} />
                ) : (
                  <DeskEmpty>Nothing yet.</DeskEmpty>
                )}
              </Card>
            ))}
          </div>
        </div>
      </WorkspacePage>
    </Shell>
  );
}

// What a companion offers, as the owner reviews it before trusting it.
export function CompanionFacts({ entry }) {
  return (
    <dl className="drawer-facts companion-facts">
      <dt>Program</dt>
      <dd className="connected-app-address">{entry.executable || 'Not given'}</dd>
      <dt>Widgets</dt>
      <dd>{(entry.widgets || []).map((widget) => widget.name).join(', ') || 'None'}</dd>
      <dt>Buttons</dt>
      <dd>{(entry.actions || []).map((action) => action.title).join(', ') || 'None'}</dd>
    </dl>
  );
}
