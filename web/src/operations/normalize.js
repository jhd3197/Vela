// One shape for every kind of background work.
//
// Origin: ServerKit `frontend/src/services/operations.js` (MIT, same owner).
// An automation run, an agent task, an update, a backup, an install and a
// health sweep are six different records from six different endpoints, and
// three surfaces — the System page, the desk's Needs you widget and the
// notification bell — each used to read whichever of them it happened to know
// about. This is where they become one list.
//
//   {
//     key,            // kind:id, stable across polls
//     kind,           // automation-run | agent-task | update | backup |
//                     // install | doctor
//     title, subtitle,
//     status,         // one of operations/status.js's seven
//     needsAttention, // waiting on a person, or failed and unacknowledged
//     progress,       // { completed, total, percent } or null
//     startedAt, updatedAt,   // ISO or null
//     href,           // where to look
//     source,         // the record it came from, for a richer detail view
//   }
//
// The rule for a field a source does not have is `null` and a note in
// `plans/DASHBOARD-FOUNDATIONS-PROGRESS.md`. Nothing here invents a timestamp
// or a step count the engine did not send.
import { operationStatus, statusLabel } from './status.js';

const text = (value) => (typeof value === 'string' && value.trim() ? value.trim() : null);

function operation(fields) {
  return {
    key: `${fields.kind}:${fields.id}`,
    kind: fields.kind,
    title: fields.title,
    subtitle: fields.subtitle ?? null,
    status: fields.status,
    needsAttention: Boolean(fields.needsAttention),
    progress: fields.progress ?? null,
    startedAt: fields.startedAt ?? null,
    updatedAt: fields.updatedAt ?? null,
    href: fields.href,
    source: fields.source ?? null,
  };
}

/**
 * An automation run. The record carries no step count, so `progress` is null;
 * the run's own page is where the steps are.
 */
export function normalizeAutomationRun(run) {
  if (!run?.id) return null;
  const status = operationStatus(run.status);
  return operation({
    kind: 'automation-run',
    id: run.id,
    title: text(run.workflowName) || 'Automation',
    subtitle: text(run.error) || statusLabel(run.status),
    status,
    // A run waiting at an approval gate is waiting on a person. A failed one
    // is unacknowledged until somebody opens it, which is all the engine
    // gives us to go on.
    needsAttention: status === 'waiting' || status === 'failed',
    startedAt: run.startedAt || run.queuedAt || null,
    updatedAt: run.finishedAt || run.startedAt || run.queuedAt || null,
    href: run.workflowId ? `/automations/${run.workflowId}` : '/automations',
    source: run,
  });
}

/**
 * One agent desktop's work, from the compact summary the rail already polls.
 * A desktop with nothing running, nothing queued and nobody waiting is not an
 * operation, so it is left out rather than listed as idle.
 */
export function normalizeAgentDesktop(desktopId, summary, desktop = null) {
  if (!desktopId || !summary) return null;
  const waiting = Number(summary.needsYou) || 0;
  const queued = Number(summary.queued) || 0;
  const blocked = text(summary.blocked);
  if (!summary.working && !waiting && !queued && !blocked) return null;

  const status = waiting || blocked ? 'waiting' : summary.working ? 'running' : 'queued';
  const parts = [];
  if (waiting) parts.push(`${waiting} to answer`);
  if (queued) parts.push(`${queued} waiting its turn`);
  if (blocked) parts.push(blocked);
  return operation({
    kind: 'agent-task',
    id: desktopId,
    title: text(desktop?.name) || 'Agent desktop',
    subtitle: parts.join(' · ') || null,
    status,
    needsAttention: Boolean(waiting || blocked),
    startedAt: null,
    updatedAt: null,
    href: `/desktops/${desktopId}`,
    source: { desktopId, ...summary },
  });
}

// The update job's own words for what it is doing. `error` is the only one
// that is not progress.
const UPDATE_RUNNING = new Set([
  'downloading',
  'verifying',
  'backing-up',
  'applying',
  'restarting',
]);

/**
 * The update Vela is installing, or the one it found. Nothing at all when the
 * server is up to date and no update is running.
 */
export function normalizeUpdate(status, job) {
  const state = String(job?.state || 'idle');
  if (UPDATE_RUNNING.has(state) || state === 'error') {
    const version = text(job?.version) || text(status?.latest);
    return operation({
      kind: 'update',
      id: version || 'running',
      title: version ? `Installing Vela ${version}` : 'Installing an update',
      subtitle: text(job?.message) || null,
      status: state === 'error' ? 'failed' : 'running',
      needsAttention: state === 'error',
      progress:
        typeof job?.percent === 'number'
          ? { completed: job.percent, total: 100, percent: job.percent }
          : null,
      startedAt: null,
      updatedAt: null,
      href: '/settings#updates',
      source: { status, job },
    });
  }
  if (!status?.available || !status?.latest) return null;
  return operation({
    kind: 'update',
    id: status.latest,
    title: `Vela ${status.latest} is available`,
    subtitle: status.current ? `You are running ${status.current}` : null,
    status: 'waiting',
    needsAttention: true,
    startedAt: null,
    updatedAt: status.checkedAt || null,
    href: '/settings#updates',
    source: { status, job },
  });
}

/** A backup that has been taken. Finished by the time anything can list it. */
export function normalizeBackup(backup) {
  if (!backup?.name) return null;
  return operation({
    kind: 'backup',
    id: backup.name,
    title: backup.safety ? 'Safety copy before a restore' : 'Backup',
    subtitle: backup.name,
    status: 'done',
    needsAttention: false,
    startedAt: backup.created_at || null,
    updatedAt: backup.created_at || null,
    href: '/settings#backups',
    source: backup,
  });
}

/**
 * The last health sweep, as one operation rather than one per check — the
 * sweep is the thing that ran.
 */
export function normalizeDoctor(doctor) {
  const checks = Array.isArray(doctor?.checks) ? doctor.checks : [];
  if (checks.length === 0) return null;
  const failed = checks.filter((check) => check.status === 'fail');
  const ranAt =
    doctor.ranAt ||
    checks
      .map((check) => check.ranAt)
      .filter(Boolean)
      .sort()
      .pop() ||
    null;
  return operation({
    kind: 'doctor',
    id: 'last-sweep',
    title: 'Health check',
    subtitle: failed.length
      ? `${failed.length} check${failed.length === 1 ? ' needs' : 's need'} attention`
      : `${checks.length} checks passed`,
    status: failed.length ? 'failed' : 'done',
    needsAttention: failed.length > 0,
    progress: {
      completed: checks.length - failed.length,
      total: checks.length,
      percent: Math.round(((checks.length - failed.length) / checks.length) * 100),
    },
    startedAt: ranAt,
    updatedAt: ranAt,
    href: '/settings#health',
    source: doctor,
  });
}

/**
 * An app being installed right now. The engine has no endpoint for this — see
 * the progress file — so the only record is the dashboard's own busy set, and
 * the timestamps are honestly null.
 */
export function normalizeInstall(app) {
  if (!app?.id) return null;
  return operation({
    kind: 'install',
    id: app.id,
    title: `Installing ${text(app.name) || app.id}`,
    subtitle: null,
    status: 'running',
    needsAttention: false,
    startedAt: null,
    updatedAt: null,
    href: '/library?tab=installed',
    source: app,
  });
}

/** Needs-attention first, then the most recently touched. A row with no */
/** timestamp sorts after one that has one rather than jumping to the top. */
export function sortOperations(operations) {
  return [...operations].sort((left, right) => {
    if (left.needsAttention !== right.needsAttention) return left.needsAttention ? -1 : 1;
    const at = (value) => (value ? Date.parse(value) || 0 : 0);
    return at(right.updatedAt) - at(left.updatedAt);
  });
}

/**
 * Every source the dashboard can read, in one list. Each argument is what the
 * matching loader returned, or nothing.
 */
export function normalizeOperations({
  runs = null,
  attention = null,
  desktops = null,
  updates = null,
  updateJob = null,
  backups = null,
  doctor = null,
  installing = null,
} = {}) {
  const byId = new Map((desktops || []).map((desktop) => [desktop.id, desktop]));
  // `/api/doctor` carries the update status too, so a surface that reads only
  // the sweep still knows about a release without a poll of its own.
  const updateStatus = updates || doctor?.update || null;
  const list = [
    ...(runs?.runs || []).map(normalizeAutomationRun),
    ...Object.entries(attention?.desktops || {}).map(([id, summary]) =>
      normalizeAgentDesktop(id, summary, byId.get(id)),
    ),
    normalizeUpdate(updateStatus, updateJob),
    ...(backups?.backups || []).map(normalizeBackup),
    normalizeDoctor(doctor),
    ...(installing || []).map(normalizeInstall),
  ].filter(Boolean);
  return sortOperations(list);
}
