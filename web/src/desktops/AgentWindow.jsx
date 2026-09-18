// The window you use to give a desktop work and to follow what it does.
//
// It looks like an app window and sits on the desk like one, and it is
// deliberately *not* one: it is first-party owner chrome. Nothing in the agent's
// browser can see it, click it or reach the routes behind it. That is the rule
// the whole separation rests on — the approval control is here, and here is
// somewhere the thing asking for approval cannot go.
//
// What it shows: what is being worked on now, what is queued behind it, what
// needs an answer, and what came of the last thing. Nothing invented — no
// progress percentage for a task that does not know how many steps it needs,
// no hidden reasoning, and a result that says plainly whether anything actually
// changed.
import { useCallback, useMemo, useRef, useState } from 'react';
import Button from '../components/ui/Button.jsx';
import { useApps } from '../store.jsx';
import { useDesktops } from './DesktopsProvider.jsx';
import AgentSetup from './AgentSetup.jsx';
import ApprovalCard from './ApprovalCard.jsx';
import RemoteView from './RemoteView.jsx';
import TaskActivity, { LIVE_STATES } from './TaskActivity.jsx';
import TaskFiles from './TaskFiles.jsx';
import { carriesApp, readAppReference } from './app-reference.js';
import { desktopsApi } from './desktopsApi.js';
import useAgentEvents from './useAgentEvents.js';
// A task's state in the words the rest of the interface uses. They live with
// the rest of the status vocabulary so this window and the operations list
// cannot drift apart.
import { agentStateLabel } from '../operations/status.js';

function Elapsed({ budget }) {
  const used = budget?.activeSeconds?.used;
  if (typeof used !== 'number') return null;
  const minutes = Math.floor(used / 60);
  return (
    <span className="task-elapsed">
      {minutes ? `${minutes}m ` : ''}
      {Math.round(used % 60)}s working
    </span>
  );
}

export default function AgentWindow({ desktopId }) {
  const { desktops, refresh: refreshDesktops, views } = useDesktops();
  const { apps } = useApps();
  const desktop = desktops?.find((entry) => entry.id === desktopId);
  const isAgent = desktop?.kind === 'agent';
  const { events, tasks, approvals, error, gap, loaded, refresh, clearGap } = useAgentEvents(
    desktopId,
    { enabled: isAgent },
  );
  const [instruction, setInstruction] = useState('');
  const [attachments, setAttachments] = useState([]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState(null);
  // One id per composed instruction. A double-tapped button or a retried
  // request joins the queue once rather than twice.
  const requestId = useRef(null);

  const active = tasks.active;
  const queued = useMemo(
    () => (tasks.runs || []).filter((run) => run.state === 'queued'),
    [tasks.runs],
  );
  const finished = useMemo(
    () => (tasks.runs || []).filter((run) => !LIVE_STATES.includes(run.state)),
    [tasks.runs],
  );

  const submit = useCallback(
    async (event) => {
      event?.preventDefault();
      const text = instruction.trim();
      if (!text) return;
      setBusy(true);
      setProblem(null);
      if (!requestId.current) requestId.current = crypto.randomUUID();
      try {
        await desktopsApi.submitTask(desktopId, {
          instruction: [
            text,
            // A dropped app is context, not an instruction and not a
            // permission. It names the app and nothing else happens because it
            // is there.
            ...attachments.map((app) => `(the ${app.name} app)`),
          ].join(' '),
          clientRequestId: requestId.current,
        });
        setInstruction('');
        setAttachments([]);
        requestId.current = null;
        await refresh();
      } catch (failure) {
        setProblem(failure);
      } finally {
        setBusy(false);
      }
    },
    [attachments, desktopId, instruction, refresh],
  );

  // Asking again is a new task. The old one is over — what it did, it did — and
  // this queues the same instruction rather than pretending to continue it.
  const again = useCallback(
    async (runId) => {
      setBusy(true);
      setProblem(null);
      try {
        await desktopsApi.retryTask(desktopId, runId);
        await refresh();
      } catch (failure) {
        setProblem(failure);
      } finally {
        setBusy(false);
      }
    },
    [desktopId, refresh],
  );

  const carryOnQueue = useCallback(async () => {
    setBusy(true);
    setProblem(null);
    try {
      await desktopsApi.resumeQueue(desktopId);
      await refresh();
    } catch (failure) {
      setProblem(failure);
    } finally {
      setBusy(false);
    }
  }, [desktopId, refresh]);

  const control = useCallback(
    async (runId, action) => {
      setBusy(true);
      setProblem(null);
      try {
        await desktopsApi.controlTask(desktopId, runId, action);
        await refresh();
      } catch (failure) {
        setProblem(failure);
      } finally {
        setBusy(false);
      }
    },
    [desktopId, refresh],
  );

  // Naming an app, from a drag or from the button beside the composer. Either
  // way it is a reference and nothing else: no install, no submission, no
  // change to what this desktop is allowed to use. The chip is visible before
  // anything is sent, and removing it is one click.
  const attach = useCallback((app) => {
    if (!app?.id) return;
    setAttachments((current) =>
      current.some((entry) => entry.id === app.id)
        ? current
        : [...current, { id: app.id, name: app.name || app.id }],
    );
  }, []);

  const onDrop = useCallback(
    (event) => {
      if (!carriesApp(event.dataTransfer)) return;
      event.preventDefault();
      attach(readAppReference(event.dataTransfer));
    },
    [attach],
  );

  if (!isAgent) {
    return (
      <div className="agent-window">
        <AgentSetup
          desktop={desktop}
          onReady={async () => {
            await refreshDesktops?.();
            await refresh();
          }}
        />
      </div>
    );
  }

  return (
    <div className="agent-window">
      <form
        className="agent-composer"
        onSubmit={submit}
        onDragOver={(e) => e.preventDefault()}
        onDrop={onDrop}
      >
        <label htmlFor="agent-instruction">What should this desktop do?</label>
        <textarea
          id="agent-instruction"
          rows={3}
          value={instruction}
          onChange={(event) => setInstruction(event.target.value)}
          placeholder="Open Notes and make a shopping list from the meal plan"
        />
        {attachments.length > 0 && (
          <ul className="agent-chips">
            {attachments.map((app) => (
              <li key={app.id}>
                {app.name}
                <button
                  type="button"
                  aria-label={`Remove ${app.name}`}
                  onClick={() =>
                    setAttachments((current) => current.filter((entry) => entry.id !== app.id))
                  }
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="form-actions">
          <Button type="submit" variant="primary" pending={busy} disabled={!instruction.trim()}>
            {active ? 'Add to the queue' : 'Start'}
          </Button>
          {/* The same thing the drag does, for anyone not using a pointer.
              Naming an app is not permission to use it: what this desktop may
              open is still only what its settings say. */}
          <label className="agent-attach">
            <span>Mention an app</span>
            <select
              value=""
              onChange={(event) => {
                const app = (apps || []).find((entry) => entry.id === event.target.value);
                if (app) attach({ id: app.id, name: app.name });
              }}
            >
              <option value="">Choose…</option>
              {(apps || [])
                .filter((app) => app.installed)
                .map((app) => (
                  <option key={app.id} value={app.id}>
                    {app.name}
                  </option>
                ))}
            </select>
          </label>
        </div>
      </form>

      {(problem || error) && (
        <p role="alert" className="agent-blocked">
          {(problem || error).message}
        </p>
      )}

      {approvals.map((request) => (
        <ApprovalCard
          key={request.requestId}
          desktopId={desktopId}
          request={request}
          onResolved={refresh}
        />
      ))}

      {active ? (
        <section className="task-card" aria-labelledby="task-current">
          <header>
            <h3 id="task-current">{active.instruction}</h3>
            <p className="task-state" role="status">
              {agentStateLabel(active.state)} <Elapsed budget={active.budget} />
            </p>
          </header>
          <div className="form-actions">
            {active.state === 'paused' ? (
              <Button size="small" disabled={busy} onClick={() => control(active.id, 'resume')}>
                Carry on
              </Button>
            ) : (
              <Button
                size="small"
                disabled={busy || !['running', 'starting'].includes(active.state)}
                onClick={() => control(active.id, 'pause')}
              >
                Pause
              </Button>
            )}
            <Button
              size="small"
              variant="danger"
              disabled={busy}
              onClick={() => control(active.id, 'stop')}
            >
              Stop
            </Button>
          </div>
          <TaskActivity events={events} gap={gap} onClearGap={clearGap} />
        </section>
      ) : (
        loaded && <p className="field-hint">Nothing is being worked on.</p>
      )}

      {/* Watching is passive. Taking over is a separate, deliberate act, and it
          pauses the task rather than racing it. */}
      <RemoteView
        desktopId={desktopId}
        views={views?.views}
        selectedViewId={views?.layout?.selectedView}
      />

      {/* The file picker is owner chrome, like the approval card: the agent is
          told which files exist and can attach one, and cannot open this. */}
      <TaskFiles desktopId={desktopId} enabled={isAgent} />

      {/* A queue that is holding says why, and moves only when the person has
          seen it. Marching on past a failure turns one problem into a row of
          them with the same cause. */}
      {tasks.blocked && queued.length > 0 && (
        <p className="queue-blocked" role="status">
          {tasks.blocked} Nothing else starts until you say so.
          <Button size="small" disabled={busy} onClick={carryOnQueue}>
            Carry on
          </Button>
        </p>
      )}

      {queued.length > 0 && (
        <section className="task-queue" aria-labelledby="task-queue-title">
          <h3 id="task-queue-title">Waiting</h3>
          <ol>
            {queued.map((run) => (
              <li key={run.id}>
                <span>{run.instruction}</span>
                <Button
                  size="small"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => control(run.id, 'stop')}
                >
                  Cancel
                </Button>
              </li>
            ))}
          </ol>
        </section>
      )}

      {finished.length > 0 && (
        <section className="task-results" aria-labelledby="task-results-title">
          <h3 id="task-results-title">Earlier</h3>
          <ol>
            {finished.slice(0, 8).map((run) => (
              <li key={run.id} data-state={run.state}>
                <p className="task-state">{agentStateLabel(run.state)}</p>
                <p className="task-instruction">{run.instruction}</p>
                {run.result?.summary && <p className="task-summary">{run.result.summary}</p>}
                {/* Read from the receipts, never from the wording. A summary
                    that reads like a change sits beside this. */}
                {run.result && (
                  <p className="task-changed">
                    {run.result.changed ? 'Something was changed.' : 'Nothing was changed.'}
                  </p>
                )}
                {run.detail && run.state !== 'succeeded' && (
                  <p className="task-detail">{run.detail}</p>
                )}
                {/* How far it actually got, from the receipts. A task that was
                    interrupted leaves the question, and the last committed step
                    is the only honest answer to it. */}
                {run.result?.lastConfirmed && (
                  <p className="task-detail">
                    The last thing it finished was {run.result.lastConfirmed.tool}
                    {run.result.lastConfirmed.target
                      ? ` on ${run.result.lastConfirmed.target}`
                      : ''}
                    .
                  </p>
                )}
                {['failed', 'cancelled', 'interrupted'].includes(run.state) && (
                  <Button size="small" disabled={busy} onClick={() => again(run.id)}>
                    Try again
                  </Button>
                )}
              </li>
            ))}
          </ol>
        </section>
      )}

      {tasks.keepingHistory === false && (
        <p className="field-hint">
          History is off, so tasks and their activity are kept only while Vela is running.
        </p>
      )}
    </div>
  );
}
