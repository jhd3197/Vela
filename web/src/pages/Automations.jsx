import { useCallback, useId, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  ArrowRight,
  CheckCircle,
  Clock,
  CloudArrowUp,
  DotsThreeVertical,
  Lightning,
  Play,
  Plus,
  Warning,
  XCircle,
} from '@phosphor-icons/react';
import WorkspacePage from '../components/WorkspacePage.jsx';
import Button from '../components/ui/Button.jsx';
import Dialog from '../components/ui/Dialog.jsx';
import EmptyState from '../components/ui/EmptyState.jsx';
import FormField from '../components/ui/FormField.jsx';
import LoadingState from '../components/ui/LoadingState.jsx';
import AutomationEditor from './AutomationEditor.jsx';
import { useResource } from '../hooks/useResource.js';
import { useAsyncAction } from '../hooks/useAsyncAction.js';
import { useConfirm } from '../hooks/useConfirm.js';
import {
  automationsApi,
  RUN_STATUS_LABELS,
  RUN_STATUS_TONE,
  WORKFLOW_STATUS_LABELS,
  triggerLabel,
} from '../automationsApi.js';
import { relTime } from '../api.js';

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'active', label: 'On' },
  { key: 'paused', label: 'Off' },
  { key: 'attention', label: 'Needs attention' },
];

const RUN_ICON = { succeeded: CheckCircle, failed: XCircle, timed_out: XCircle };

// `/automations` lists what is saved on this server; `/automations/:id` opens
// the same page's editor. Nothing here is a sample: an empty installation shows
// an empty state.
export default function Automations() {
  const { workflowId } = useParams();
  if (workflowId) return <AutomationEditor />;
  return <AutomationList />;
}

function AutomationList() {
  const load = useCallback((options) => automationsApi.list(options), []);
  const loadRuns = useCallback((options) => automationsApi.runs({ limit: 8 }, options), []);
  const loadBlueprints = useCallback((options) => automationsApi.blueprints(options), []);
  const { data, error, loading, refresh } = useResource(load, { intervalMs: 15000 });
  const { data: recent, refresh: refreshRuns } = useResource(loadRuns, { intervalMs: 15000 });
  const { data: blueprints } = useResource(loadBlueprints);
  const navigate = useNavigate();
  const action = useAsyncAction();
  const confirm = useConfirm();
  const [filter, setFilter] = useState('all');
  const [creating, setCreating] = useState(false);
  const [menuFor, setMenuFor] = useState(null);
  const fileInput = useRef(null);
  const headingId = useId();

  const automations = useMemo(() => data?.automations ?? [], [data]);
  const visible = useMemo(() => {
    if (filter === 'active') return automations.filter((item) => item.status === 'active');
    if (filter === 'paused')
      return automations.filter((item) => item.status === 'paused' || item.status === 'draft');
    if (filter === 'attention') return automations.filter((item) => item.needsAttention);
    return automations;
  }, [automations, filter]);

  const runtime = data?.status;
  const statistics = data?.statistics;

  const importFile = async (file) => {
    const text = await file.text();
    let payload;
    try {
      payload = JSON.parse(text);
    } catch {
      throw new Error('That file is not valid JSON.');
    }
    return automationsApi.importOne(payload);
  };

  return (
    <WorkspacePage>
      <div className="page-inner">
        <header className="page-head-row">
          <div>
            <h1 className="page-title">Automations</h1>
            <p className="page-sub">
              Steps wired between your apps, running on this computer. Vela has to be running for
              them to happen.
            </p>
          </div>
          <div className="automation-actions">
            <input
              ref={fileInput}
              type="file"
              accept="application/json,.json"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) action.run(() => importFile(file).then(refresh));
              }}
            />
            <Button onClick={() => fileInput.current?.click()} pending={action.pending}>
              <CloudArrowUp size={15} /> Import
            </Button>
            <Button variant="primary" onClick={() => setCreating(true)}>
              <Plus size={15} /> New automation
            </Button>
          </div>
        </header>

        {error && (
          <div className="banner banner-error" role="alert">
            <div>
              <strong>{error.message}</strong>
            </div>
            <Button onClick={refresh}>Retry</Button>
          </div>
        )}
        {action.error && (
          <p className="automation-error" role="alert">
            {action.error.message}
          </p>
        )}
        {runtime && !runtime.available && (
          <div className="banner banner-error" role="alert">
            <div>
              <strong>Automations cannot run on this server yet.</strong>
              <p>{runtime.detail}</p>
            </div>
          </div>
        )}

        <div className="auto-columns">
          <div className="auto-main">
            <div className="seg" role="tablist" style={{ alignSelf: 'flex-start' }}>
              {FILTERS.map((item) => (
                <button
                  key={item.key}
                  role="tab"
                  aria-selected={filter === item.key}
                  className={`seg-opt${filter === item.key ? ' seg-opt-active' : ''}`}
                  onClick={() => setFilter(item.key)}
                >
                  {item.label}
                </button>
              ))}
            </div>

            {loading && !data && <LoadingState>Loading your automations…</LoadingState>}

            {!loading && automations.length === 0 && (
              <EmptyState
                title="No automations yet"
                description="Build one by choosing a trigger and adding steps. Vela runs it on this computer, including while the browser is closed."
              >
                <Button variant="primary" onClick={() => setCreating(true)}>
                  <Plus size={15} /> New automation
                </Button>
              </EmptyState>
            )}

            {!loading && automations.length > 0 && visible.length === 0 && (
              <EmptyState title="Nothing here" description="No automations match this filter." />
            )}

            <div className="auto-list">
              {visible.map((item) => (
                <AutomationCard
                  key={item.id}
                  item={item}
                  pending={action.pending}
                  menuOpen={menuFor === item.id}
                  onMenu={() => setMenuFor((current) => (current === item.id ? null : item.id))}
                  onToggle={() =>
                    action.run(async () => {
                      if (item.status === 'active') await automationsApi.pause(item.id);
                      else await automationsApi.activate(item.id);
                      refresh();
                    })
                  }
                  onRun={() =>
                    action.run(async () => {
                      await automationsApi.startRun(item.id);
                      refreshRuns();
                      refresh();
                    })
                  }
                  onDuplicate={() =>
                    action.run(async () => {
                      await automationsApi.duplicate(item.id);
                      setMenuFor(null);
                      refresh();
                    })
                  }
                  onArchive={() =>
                    action.run(async () => {
                      await automationsApi.archive(item.id);
                      setMenuFor(null);
                      refresh();
                    })
                  }
                  onDelete={async () => {
                    // Permanent, and it was one click away from the menu.
                    const sure = await confirm({
                      title: `Delete ${item.name}?`,
                      message:
                        'The automation and its run history go. Anything it already did stays ' +
                        'done. This cannot be undone.',
                      confirmText: 'Delete permanently',
                      pendingText: 'Deleting…',
                    });
                    if (!sure) return;
                    action.run(async () => {
                      await automationsApi.remove(item.id);
                      setMenuFor(null);
                      refresh();
                    });
                  }}
                />
              ))}
            </div>

            {blueprints?.blueprints?.length > 0 && (
              <section className="blueprints">
                <div>
                  <h2 className="blueprints-title">Start from a blueprint</h2>
                  <p className="blueprints-sub">
                    Each one is a draft you can change. Vela only offers the ones this server can
                    actually run, and nothing is allowed until you review it.
                  </p>
                </div>
                <div className="blueprints-actions">
                  {blueprints.blueprints.map((blueprint) => (
                    <Button
                      key={blueprint.id}
                      pending={action.pending}
                      disabled={!blueprint.available}
                      title={blueprint.requirement || blueprint.description}
                      onClick={() =>
                        action.run(async () => {
                          const created = await automationsApi.useBlueprint(blueprint.id);
                          refresh();
                          navigate(`/automations/${created.id}`);
                        })
                      }
                    >
                      <Lightning size={15} weight="fill" />
                      {blueprint.name}
                    </Button>
                  ))}
                </div>
                {blueprints.blueprints.some((blueprint) => !blueprint.available) && (
                  <p className="panel-note">
                    {blueprints.blueprints.find((blueprint) => !blueprint.available).requirement}
                  </p>
                )}
              </section>
            )}
          </div>

          <aside className="activity-rail" aria-labelledby={headingId}>
            <h2 id={headingId} className="section-head">
              Recent runs
            </h2>
            <div className="activity-list">
              {(recent?.runs ?? []).map((item) => {
                const Icon = RUN_ICON[item.status] || Clock;
                return (
                  <Link
                    key={item.id}
                    className="activity-item"
                    to={`/automations/${item.workflowId}`}
                  >
                    <Icon
                      size={14}
                      weight="fill"
                      className={`run-tone-${RUN_STATUS_TONE[item.status] || 'neutral'}`}
                    />
                    <span className="activity-item-text">
                      <span>{item.workflowName || 'Removed automation'}</span>
                      <span className="activity-item-sub">
                        {RUN_STATUS_LABELS[item.status] || item.status} · {relTime(item.queuedAt)}
                      </span>
                    </span>
                  </Link>
                );
              })}
              {!recent?.runs?.length && <p className="panel-note">Nothing has run yet.</p>}
            </div>
            <span className="activity-divider" />
            <div className="activity-stats">
              <span className="activity-stat">
                <span>Runs this week</span>
                <span>{statistics ? statistics.total : '—'}</span>
              </span>
              <span className="activity-stat">
                <span>Failures</span>
                <span>{statistics ? statistics.failed : '—'}</span>
              </span>
              <span className="activity-stat">
                <span>Average run</span>
                <span>
                  {statistics?.averageSeconds != null ? `${statistics.averageSeconds}s` : '—'}
                </span>
              </span>
            </div>
            {runtime?.notifications && (
              <p className="panel-note">
                {runtime.notifications.configured
                  ? `Notification steps publish to ${runtime.notifications.server}/${runtime.notifications.topic}.`
                  : 'Notification steps need a notification server in Settings.'}
              </p>
            )}
          </aside>
        </div>
      </div>

      <CreateDialog
        open={creating}
        pending={action.pending}
        onClose={() => setCreating(false)}
        onCreate={(name) =>
          action.run(async () => {
            const created = await automationsApi.create(name);
            setCreating(false);
            refresh();
            navigate(`/automations/${created.id}`);
          })
        }
      />
    </WorkspacePage>
  );
}

function AutomationCard({
  item,
  pending,
  menuOpen,
  onMenu,
  onToggle,
  onRun,
  onDuplicate,
  onArchive,
  onDelete,
}) {
  const on = item.status === 'active';
  return (
    <article className={`auto-card${on ? '' : ' auto-card-paused'}`}>
      <div className="auto-head">
        <span className="auto-glyph" aria-hidden="true">
          <Lightning size={17} weight="fill" />
        </span>
        <span className="auto-head-text">
          <Link className="auto-title" to={`/automations/${item.id}`}>
            {item.name}
          </Link>
          <span className="auto-sub">
            {WORKFLOW_STATUS_LABELS[item.status]} · {triggerLabel(item.trigger)}
            {item.schedule ? ` · ${item.schedule.summary}` : ''} · {item.steps} step
            {item.steps === 1 ? '' : 's'}
          </span>
        </span>
        <span className="auto-side">
          <button
            className={`switch${on ? ' switch-on' : ''}`}
            role="switch"
            aria-checked={on}
            disabled={pending || item.status === 'archived'}
            aria-label={`${on ? 'Turn off' : 'Turn on'} ${item.name}`}
            onClick={onToggle}
          />
          <Button
            size="small"
            variant="ghost"
            pending={pending}
            onClick={onRun}
            aria-label={`Run ${item.name}`}
          >
            <Play size={14} weight="fill" />
          </Button>
          <Button
            size="small"
            variant="ghost"
            aria-label={`More actions for ${item.name}`}
            aria-expanded={menuOpen}
            onClick={onMenu}
          >
            <DotsThreeVertical size={18} />
          </Button>
          {menuOpen && (
            <div className="automation-menu" role="menu">
              <button role="menuitem" onClick={onDuplicate}>
                Duplicate
              </button>
              <button role="menuitem" onClick={onArchive}>
                Archive
              </button>
              <button role="menuitem" className="automation-menu-danger" onClick={onDelete}>
                Delete permanently
              </button>
            </div>
          )}
        </span>
      </div>

      {item.description && <p className="auto-description">{item.description}</p>}

      <div className="auto-steps">
        {item.hasUnpublishedChanges && (
          <span className="tag tag-accent">Edited since it was turned on</span>
        )}
        {item.grants
          ?.filter((grant) => grant.available)
          .map((grant) => (
            <span key={`${grant.app}:${grant.action}`} className="step-chip">
              <ArrowRight size={13} />
              {grant.appName} · {grant.title}
              {!grant.granted && <Warning size={13} className="run-tone-warn" />}
            </span>
          ))}
      </div>

      {item.problems?.length > 0 && (
        <p className="auto-problem">
          <Warning size={14} weight="fill" /> {item.problems[0].detail}
        </p>
      )}

      {item.lastRun && (
        <p className="auto-lastrun">
          Last run: {RUN_STATUS_LABELS[item.lastRun.status] || item.lastRun.status} ·{' '}
          {relTime(item.lastRun.queuedAt)}
          {item.lastRun.error ? ` · ${item.lastRun.error}` : ''}
        </p>
      )}
    </article>
  );
}

function CreateDialog({ open, pending, onClose, onCreate }) {
  const cancel = useRef(null);
  const headingId = useId();
  const [name, setName] = useState('');
  if (!open) return null;
  return (
    <Dialog
      open
      onClose={onClose}
      pending={pending}
      initialFocusRef={cancel}
      aria-labelledby={headingId}
    >
      <h2 id={headingId}>New automation</h2>
      <div className="field">
        <FormField label="Name" hint="You can rename it later.">
          <input
            value={name}
            maxLength={120}
            placeholder="Weekly summary"
            onChange={(event) => setName(event.target.value)}
          />
        </FormField>
      </div>
      <div className="dialog-actions">
        <Button
          variant="primary"
          pending={pending}
          disabled={!name.trim()}
          onClick={() => onCreate(name.trim())}
        >
          Create
        </Button>
        <Button ref={cancel} disabled={pending} onClick={onClose}>
          Cancel
        </Button>
      </div>
    </Dialog>
  );
}
