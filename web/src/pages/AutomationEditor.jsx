import { Suspense, lazy, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  ArrowsClockwise,
  CloudArrowDown,
  Copy,
  DotsThreeVertical,
  Play,
  Warning,
} from '@phosphor-icons/react';
import WorkspacePage from '../components/WorkspacePage.jsx';
import Button from '../components/ui/Button.jsx';
import Dialog from '../components/ui/Dialog.jsx';
import LoadingState from '../components/ui/LoadingState.jsx';
import FormField from '../components/ui/FormField.jsx';
// The visual editor carries its own renderer and icon set. Loading it only on
// this route keeps it out of every other page's download.
const AutomationCanvas = lazy(() => import('../components/automations/AutomationCanvas.jsx'));
import GrantReview from '../components/automations/GrantReview.jsx';
import RunDetail from '../components/automations/RunDetail.jsx';
import { useResource } from '../hooks/useResource.js';
import { useAsyncAction } from '../hooks/useAsyncAction.js';
import { automationsApi, triggerLabel } from '../automationsApi.js';
import { statusLabel } from '../operations/status.js';
import { relTime } from '../api.js';

const LIVE = new Set(['queued', 'running', 'waiting']);

const SAVE_LABEL = {
  idle: '',
  pending: 'Unsaved changes',
  saving: 'Saving…',
  saved: 'Saved',
  error: 'Not saved',
};

export default function AutomationEditor() {
  const { workflowId } = useParams();
  const navigate = useNavigate();
  const titleId = useId();

  const loadDetail = useCallback(
    (options) => automationsApi.get(workflowId, options),
    [workflowId],
  );
  const loadCatalog = useCallback((options) => automationsApi.catalog(options), []);
  const { data: detail, error, loading, refresh } = useResource(loadDetail);
  const { data: catalog } = useResource(loadCatalog);

  const action = useAsyncAction();
  const [saveState, setSaveState] = useState({ status: 'idle', error: null });
  const [saveError, setSaveError] = useState(null);
  const [runId, setRunId] = useState(null);
  const [run, setRun] = useState(null);
  const [renaming, setRenaming] = useState(false);
  const [webhookSecret, setWebhookSecret] = useState(null);
  const [menuOpen, setMenuOpen] = useState(false);

  // The revision the editor is currently writing against. `detail` supplies the
  // first one; every accepted save advances it.
  const revision = useRef(null);
  const [baseRevision, setBaseRevision] = useState(null);
  useEffect(() => {
    if (detail && baseRevision === null) {
      revision.current = detail.documentRevision;
      setBaseRevision(detail.documentRevision);
    }
  }, [detail, baseRevision]);

  const save = useCallback(
    async (document) => {
      setSaveError(null);
      try {
        const next = await automationsApi.save(workflowId, {
          revision: revision.current,
          document,
        });
        revision.current = next.documentRevision;
        setSaveError(null);
        refresh();
        return next;
      } catch (failure) {
        setSaveError(failure);
        throw failure;
      }
    },
    [workflowId, refresh],
  );

  // Warn before leaving with an unsaved or failed save, so edits are not lost.
  useEffect(() => {
    const dirty = saveState.status === 'pending' || saveState.status === 'error';
    if (!dirty) return undefined;
    const warn = (event) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [saveState.status]);

  // Poll one run while it is live, then stop. No stream, no token in a URL.
  useEffect(() => {
    if (!runId) return undefined;
    let cancelled = false;
    let timer;
    const tick = async () => {
      try {
        const next = await automationsApi.run(runId);
        if (cancelled) return;
        setRun(next);
        if (LIVE.has(next.status)) timer = setTimeout(tick, 1200);
        else refresh();
      } catch {
        if (!cancelled) timer = setTimeout(tick, 4000);
      }
    };
    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [runId, refresh]);

  const loadRuns = useCallback(
    (options) => automationsApi.runs({ workflowId, limit: 15 }, options),
    [workflowId],
  );
  const { data: runs, refresh: refreshRuns } = useResource(loadRuns);
  useEffect(() => {
    if (run && !LIVE.has(run.status)) refreshRuns();
  }, [run?.status, run, refreshRuns]);

  const problems = detail?.draftProblems ?? [];
  const blockedGrants = (detail?.grants ?? []).filter((grant) => !grant.granted);
  const dirty = saveState.status === 'pending' || saveState.status === 'saving';
  const canRun = detail && !problems.length && !dirty && detail.status !== 'archived';

  const events = useMemo(() => run?.events ?? [], [run]);

  const startRun = () =>
    action.run(async () => {
      const started = await automationsApi.startRun(workflowId);
      setRunId(started.id);
      setRun(started);
      return started;
    });

  const toggle = () =>
    action.run(async () => {
      const next =
        detail.status === 'active'
          ? await automationsApi.pause(workflowId)
          : await automationsApi.activate(workflowId);
      refresh();
      return next;
    });

  const allow = (grant) =>
    action.run(async () => {
      await automationsApi.setGrant(workflowId, {
        app: grant.app,
        action: grant.action,
        allow: true,
        requestContract: grant.requestContract,
        targetContract: grant.targetContract,
      });
      refresh();
    });

  const revoke = (grant) =>
    action.run(async () => {
      await automationsApi.setGrant(workflowId, {
        app: grant.app,
        action: grant.action,
        allow: false,
      });
      refresh();
    });

  const exportFile = () =>
    action.run(async () => {
      const payload = await automationsApi.exportOne(workflowId);
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `${(detail?.name || 'automation').replace(/[^\w-]+/g, '-')}.vela-automation.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    });

  if (loading && !detail) {
    return (
      <WorkspacePage title="Automation" scroll={false} className="automation-editor">
        <div className="page-inner">
          <LoadingState>Loading this automation…</LoadingState>
        </div>
      </WorkspacePage>
    );
  }
  if (error) {
    return (
      <WorkspacePage title="Automation" scroll={false} className="automation-editor">
        <div className="page-inner">
          <div className="banner banner-error" role="alert">
            <div>
              <strong>{error.message}</strong>
            </div>
            <Button onClick={refresh}>Retry</Button>
          </div>
          <Link className="btn" to="/automations">
            Back to automations
          </Link>
        </div>
      </WorkspacePage>
    );
  }
  if (!detail || !catalog) return null;

  return (
    <WorkspacePage
      title={detail.name}
      subtitle={triggerLabel(detail.trigger)}
      scroll={false}
      search={false}
      className="automation-editor"
      lead={
        <Link
          className="btn btn-ghost btn-small"
          to="/automations"
          aria-label="Back to automations"
        >
          <ArrowLeft size={15} />
        </Link>
      }
      actions={
        <div className="automation-actions">
          <span className={`save-state save-state-${saveState.status}`} role="status">
            {saveError ? saveError.message : SAVE_LABEL[saveState.status]}
          </span>
          <Button
            pending={action.pending}
            disabled={!canRun}
            title={
              problems.length
                ? problems[0].detail
                : dirty
                  ? 'Wait for your changes to save first.'
                  : 'Run this now'
            }
            onClick={startRun}
          >
            <Play size={15} weight="fill" /> Run
          </Button>
          <Button
            variant={detail.status === 'active' ? undefined : 'primary'}
            pending={action.pending}
            disabled={detail.status === 'archived'}
            onClick={toggle}
          >
            {detail.status === 'active' ? 'Turn off' : 'Turn on'}
          </Button>
          <Button
            variant="ghost"
            aria-label="More actions"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          >
            <DotsThreeVertical size={18} />
          </Button>
          {menuOpen && (
            <div className="automation-menu" role="menu">
              <button
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  setRenaming(true);
                }}
              >
                Rename
              </button>
              <button
                role="menuitem"
                onClick={() =>
                  action.run(async () => {
                    const copy = await automationsApi.duplicate(workflowId);
                    setMenuOpen(false);
                    navigate(`/automations/${copy.id}`);
                  })
                }
              >
                <Copy size={14} /> Duplicate
              </button>
              <button
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  exportFile();
                }}
              >
                <CloudArrowDown size={14} /> Export
              </button>
              {detail.trigger === 'webhook' && detail.status === 'active' && (
                <button
                  role="menuitem"
                  onClick={() =>
                    action.run(async () => {
                      const rotated = await automationsApi.rotateWebhook(workflowId);
                      setMenuOpen(false);
                      setWebhookSecret(rotated);
                      refresh();
                    })
                  }
                >
                  <ArrowsClockwise size={14} /> New web address
                </button>
              )}
              <button
                role="menuitem"
                className="automation-menu-danger"
                onClick={() =>
                  action.run(async () => {
                    await automationsApi.archive(workflowId);
                    setMenuOpen(false);
                    navigate('/automations');
                  })
                }
              >
                Archive
              </button>
            </div>
          )}
        </div>
      }
    >
      <div className="automation-editor-body">
        {baseRevision !== null && (
          <Suspense fallback={<LoadingState>Loading the editor…</LoadingState>}>
            <AutomationCanvas
              workflowId={workflowId}
              catalog={catalog}
              document={detail.document}
              revision={baseRevision}
              readOnly={detail.status === 'archived'}
              onSave={save}
              onSaveStateChange={setSaveState}
              events={events}
            />
          </Suspense>
        )}

        <aside className="automation-side">
          {saveError && (
            <p className="automation-error" role="alert">
              {saveError.message}
              {saveError.status === 409 && (
                <>
                  {' '}
                  <Button size="small" onClick={() => window.location.reload()}>
                    Reload
                  </Button>
                </>
              )}
            </p>
          )}

          {problems.length > 0 && (
            <section className="panel automation-problems">
              <h2 className="section-head">
                <Warning size={15} weight="fill" /> Before this can run
              </h2>
              <ul>
                {problems.map((problem) => (
                  <li key={`${problem.nodeId ?? ''}${problem.detail}`}>{problem.detail}</li>
                ))}
              </ul>
            </section>
          )}

          <GrantReview
            grants={detail.grants}
            pending={action.pending}
            error={action.error}
            onAllow={allow}
            onRevoke={revoke}
          />

          {detail.schedule && (
            <section className="panel">
              <h2 className="section-head">Schedule</h2>
              <p>{detail.schedule.summary}</p>
              <p className="panel-note">
                {detail.schedule.nextRun
                  ? `Next run ${relTime(detail.schedule.nextRun)}.`
                  : 'Turn this automation on to start the schedule.'}{' '}
                Vela must be running at that moment. Occurrences that pass while it is off are
                skipped, not queued.
              </p>
            </section>
          )}

          {detail.webhook && (
            <section className="panel">
              <h2 className="section-head">Web address</h2>
              {detail.webhook.configured ? (
                <>
                  <code className="mono automation-hook">{detail.webhook.path}</code>
                  <p className="panel-note">
                    Send a POST with the <code className="mono">X-Vela-Automation-Secret</code>{' '}
                    header. This works wherever Vela already listens; Vela does not publish it to
                    the internet.
                  </p>
                </>
              ) : (
                <p className="panel-note">{detail.webhook.detail}</p>
              )}
            </section>
          )}

          <section className="panel automation-runs">
            <h2 className="section-head">Runs</h2>
            {run ? (
              <RunDetail
                run={run}
                catalog={catalog}
                pending={action.pending}
                onCancel={(target) => action.run(() => automationsApi.cancelRun(target))}
                onDecide={(target, gate, approved) =>
                  action.run(async () => {
                    const next = await automationsApi.decide(target, gate, approved);
                    setRun(next);
                    setRunId(target);
                  })
                }
              />
            ) : (
              <p className="panel-note">Run this automation to see what happens, step by step.</p>
            )}
            <ul className="run-history">
              {(runs?.runs ?? []).map((item) => (
                <li key={item.id}>
                  <button
                    className={`run-history-row${item.id === runId ? ' run-history-row-active' : ''}`}
                    onClick={() => setRunId(item.id)}
                  >
                    <span>{statusLabel(item.status)}</span>
                    <span className="run-history-when">{relTime(item.queuedAt)}</span>
                  </button>
                </li>
              ))}
              {!runs?.runs?.length && <li className="panel-note">No runs yet.</li>}
            </ul>
          </section>

          {blockedGrants.length > 0 && detail.status !== 'active' && (
            <p className="panel-note">Allow every request above before turning this on.</p>
          )}
        </aside>
      </div>

      <RenameDialog
        open={renaming}
        detail={detail}
        pending={action.pending}
        titleId={titleId}
        onClose={() => setRenaming(false)}
        onSave={(name, description) =>
          action.run(async () => {
            await automationsApi.rename(workflowId, name, description);
            setRenaming(false);
            refresh();
          })
        }
      />

      <SecretDialog secret={webhookSecret} onClose={() => setWebhookSecret(null)} />
    </WorkspacePage>
  );
}

function RenameDialog({ open, detail, pending, titleId, onClose, onSave }) {
  const cancel = useRef(null);
  const [name, setName] = useState(detail.name);
  const [description, setDescription] = useState(detail.description || '');
  useEffect(() => {
    if (open) {
      setName(detail.name);
      setDescription(detail.description || '');
    }
  }, [open, detail.name, detail.description]);
  if (!open) return null;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      pending={pending}
      initialFocusRef={cancel}
      aria-labelledby={titleId}
    >
      <h2 id={titleId}>Rename automation</h2>
      <div className="field">
        <FormField label="Name">
          <input value={name} maxLength={120} onChange={(event) => setName(event.target.value)} />
        </FormField>
      </div>
      <div className="field">
        <FormField label="Description" hint="Optional. Shown in the list.">
          <textarea
            rows={3}
            maxLength={500}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </FormField>
      </div>
      <div className="dialog-actions">
        <Button
          variant="primary"
          pending={pending}
          disabled={!name.trim()}
          onClick={() => onSave(name.trim(), description)}
        >
          Save
        </Button>
        <Button ref={cancel} disabled={pending} onClick={onClose}>
          Cancel
        </Button>
      </div>
    </Dialog>
  );
}

function SecretDialog({ secret, onClose }) {
  const close = useRef(null);
  const headingId = useId();
  if (!secret) return null;
  return (
    <Dialog open onClose={onClose} initialFocusRef={close} aria-labelledby={headingId}>
      <h2 id={headingId}>New web address</h2>
      <p>Copy this secret now. Vela does not show it again.</p>
      <code className="mono automation-hook">{secret.path}</code>
      <code className="mono automation-hook">{secret.secret}</code>
      <p className="panel-note">{secret.detail}</p>
      <div className="dialog-actions">
        <Button ref={close} variant="primary" onClick={onClose}>
          Done
        </Button>
      </div>
    </Dialog>
  );
}
