import { CheckCircle, Prohibit, Warning, XCircle } from '@phosphor-icons/react';
import Button from '../ui/Button.jsx';
import { RUN_STATUS_LABELS, RUN_STATUS_TONE } from '../../automationsApi.js';
import { relTime } from '../../api.js';
import { describeOutput } from './describeOutput.js';

const LEVEL_ICON = { error: XCircle, warn: Warning };

// Node ids are stable but unreadable. Show the step's own name where the run's
// document is still stored, and fall back to the id when it is not.
function stepNamesFor(document, catalog) {
  const definitions = new Map((catalog?.nodes ?? []).map((node) => [node.id, node.name]));
  const names = {};
  for (const node of document?.nodes ?? []) {
    names[node.id] = node.label || definitions.get(node.type) || node.type;
  }
  return names;
}

// What actually happened, in order, with nothing invented. A run that was cut
// short says so, and says that finished steps were not undone.
export default function RunDetail({ run, catalog, onCancel, onDecide, pending }) {
  const stepNames = stepNamesFor(run?.document, catalog);
  if (!run) return null;
  const live = ['queued', 'running', 'waiting'].includes(run.status);
  const pendingApprovals = (run.approvals || []).filter((item) => item.status === 'pending');
  return (
    <div className="run-detail">
      <header className="run-detail-head">
        <span className={`run-status run-status-${RUN_STATUS_TONE[run.status] || 'neutral'}`}>
          {RUN_STATUS_LABELS[run.status] || run.status}
        </span>
        <span className="run-detail-meta">
          Version {run.revision} · started {run.startedAt ? relTime(run.startedAt) : 'not yet'}
          {run.finishedAt ? ` · ended ${relTime(run.finishedAt)}` : ''}
        </span>
        {live && (
          <Button size="small" pending={pending} onClick={() => onCancel(run.id)}>
            Cancel
          </Button>
        )}
      </header>

      {run.error && (
        <p className="automation-error" role="alert">
          {run.error}
        </p>
      )}
      {run.status === 'cancelled' && (
        <p className="panel-note">
          Steps that had already finished were not undone. Vela stopped the run before anything
          further could happen.
        </p>
      )}
      {run.status === 'interrupted' && (
        <p className="panel-note">
          Vela could not confirm how this run ended. Check the app it writes to before running it
          again.
        </p>
      )}
      {run.documentUnavailable && (
        <p className="panel-note">The version this run used is no longer stored.</p>
      )}

      {pendingApprovals.map((approval) => (
        <div key={approval.gateKey} className="run-approval">
          <p>{approval.message}</p>
          <div className="run-approval-actions">
            <Button
              variant="primary"
              size="small"
              pending={pending}
              onClick={() => onDecide(run.id, approval.gateKey, true)}
            >
              Approve
            </Button>
            <Button
              size="small"
              pending={pending}
              onClick={() => onDecide(run.id, approval.gateKey, false)}
            >
              Reject
            </Button>
          </div>
          {approval.expiresAt && (
            <p className="panel-note">Expires {relTime(approval.expiresAt)}.</p>
          )}
        </div>
      ))}

      <ol className="run-events">
        {(run.events || []).map((event) => (
          <li key={event.seq} className={`run-event run-event-${event.type}`}>
            <EventLine event={event} names={stepNames} />
          </li>
        ))}
        {!run.events?.length && <li className="run-event panel-note">No steps ran.</li>}
      </ol>
    </div>
  );
}

function EventLine({ event, names }) {
  const step = (id) => <span className="run-event-node">{names[id] || id}</span>;
  if (event.type === 'run-start')
    return <span>Started · {event.nodeOrder?.length ?? 0} steps planned</span>;
  if (event.type === 'run-end')
    return (
      <span>
        {event.ok ? 'Finished' : 'Stopped'}
        {event.error ? ` · ${event.error}` : ''}
      </span>
    );
  if (event.type === 'run-suspended') return <span>Waiting for your decision</span>;
  if (event.type === 'node-start') return <span>{step(event.nodeId)} started</span>;
  if (event.type === 'node-success')
    return (
      <span>
        <CheckCircle size={13} weight="fill" className="run-event-good" /> {step(event.nodeId)}{' '}
        finished{typeof event.durationMs === 'number' ? ` in ${event.durationMs} ms` : ''}
        {describeOutput(event.output) ? ` · produced ${describeOutput(event.output)}` : ''}
      </span>
    );
  if (event.type === 'node-error')
    return (
      <span>
        <XCircle size={13} weight="fill" className="run-event-bad" /> {step(event.nodeId)} failed ·{' '}
        {event.error}
      </span>
    );
  if (event.type === 'node-skip')
    return (
      <span>
        <Prohibit size={13} /> {step(event.nodeId)} skipped · {event.reason}
      </span>
    );
  if (event.type === 'node-waiting')
    return <span>{step(event.nodeId)} is waiting for your approval</span>;
  if (event.type === 'node-log') {
    const Icon = LEVEL_ICON[event.level];
    return (
      <span>
        {Icon && <Icon size={13} />} {step(event.nodeId)} {event.message}
        {event.data !== undefined && (
          <code className="mono run-event-data">{String(event.data)}</code>
        )}
      </span>
    );
  }
  if (event.type === 'log-truncated') return <span className="panel-note">{event.detail}</span>;
  return <span>{event.type}</span>;
}
