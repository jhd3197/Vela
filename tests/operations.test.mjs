// The operations shape, against payloads captured from the real endpoints.
//
// Every fixture in tests/fixtures/operations was written by
// `scripts/capture-operations-fixtures.py` against a disposable Vela on a
// temporary data directory. When an endpoint changes shape, recapture them and
// this test says what moved.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  normalizeAgentDesktop,
  normalizeAutomationRun,
  normalizeBackup,
  normalizeDoctor,
  normalizeInstall,
  normalizeOperations,
  normalizeUpdate,
  sortOperations,
} from '../web/src/operations/normalize.js';
import {
  STATUSES,
  agentStateLabel,
  agentStateStatus,
  operationStatus,
  statusDotState,
  statusLabel,
  statusTone,
} from '../web/src/operations/status.js';

const fixture = (name) =>
  JSON.parse(readFileSync(new URL(`./fixtures/operations/${name}.json`, import.meta.url), 'utf8'))
    .payload;

const RUNS = fixture('automation-runs');
const ATTENTION = fixture('desktops-attention');
const UPDATES = fixture('updates');
const UPDATE_JOB = fixture('updates-job');
const BACKUPS = fixture('backups');
const DOCTOR = fixture('doctor');
const APPS = fixture('apps');

// Every operation carries the same fields, whatever it came from.
function assertShape(operation) {
  assert.deepEqual(
    Object.keys(operation).sort(),
    [
      'href',
      'kind',
      'key',
      'needsAttention',
      'progress',
      'source',
      'startedAt',
      'status',
      'subtitle',
      'title',
      'updatedAt',
    ].sort(),
  );
  assert.ok(STATUSES.includes(operation.status), `${operation.status} is not one of the seven`);
  assert.equal(operation.key, `${operation.kind}:${operation.key.split(':').slice(1).join(':')}`);
  assert.equal(typeof operation.title, 'string');
  assert.equal(typeof operation.needsAttention, 'boolean');
  assert.ok(operation.href.startsWith('/'));
  for (const field of ['startedAt', 'updatedAt']) {
    assert.ok(
      operation[field] === null || !Number.isNaN(Date.parse(operation[field])),
      `${field} is neither null nor a date: ${operation[field]}`,
    );
  }
}

test('an automation run keeps its name, its times and where to look', () => {
  const run = RUNS.runs[0];
  const operation = normalizeAutomationRun(run);
  assertShape(operation);
  assert.equal(operation.kind, 'automation-run');
  assert.equal(operation.key, `automation-run:${run.id}`);
  assert.equal(operation.title, 'Nightly greeting');
  assert.equal(operation.status, 'done');
  assert.equal(operation.needsAttention, false);
  assert.equal(operation.startedAt, run.startedAt);
  assert.equal(operation.updatedAt, run.finishedAt);
  assert.equal(operation.href, `/automations/${run.workflowId}`);
  assert.equal(operation.source, run, 'the record is kept for a richer view');
  // Recorded gap: the run record carries no step count, so nothing is invented.
  assert.equal(operation.progress, null);
});

test('a run waiting at a gate, and one that failed, both ask for a person', () => {
  const run = RUNS.runs[0];
  const waiting = normalizeAutomationRun({ ...run, status: 'waiting', finishedAt: null });
  assert.equal(waiting.status, 'waiting');
  assert.equal(waiting.needsAttention, true);
  assert.equal(waiting.updatedAt, run.startedAt);

  const failed = normalizeAutomationRun({ ...run, status: 'failed', error: 'The step gave up.' });
  assert.equal(failed.status, 'failed');
  assert.equal(failed.needsAttention, true);
  assert.equal(failed.subtitle, 'The step gave up.');

  // An engine status the shape has no word of its own for still lands in it.
  assert.equal(normalizeAutomationRun({ ...run, status: 'timed_out' }).status, 'failed');
  assert.equal(normalizeAutomationRun({ ...run, status: 'timed_out' }).subtitle, 'Took too long');
});

test('an idle agent desktop is not an operation; a busy one is', () => {
  const [id, summary] = Object.entries(ATTENTION.desktops)[0];
  assert.deepEqual(summary, {
    state: 'idle',
    runId: null,
    working: false,
    needsYou: 0,
    queued: 0,
    blocked: null,
  });
  assert.equal(normalizeAgentDesktop(id, summary), null, 'idle is not work');

  const working = normalizeAgentDesktop(id, { ...summary, working: true, state: 'running' });
  assertShape(working);
  assert.equal(working.kind, 'agent-task');
  assert.equal(working.status, 'running');
  assert.equal(working.needsAttention, false);
  assert.equal(working.href, `/desktops/${id}`);
  assert.equal(working.title, 'Agent desktop', 'the name comes from the desktop record');
  assert.equal(
    normalizeAgentDesktop(id, summary, { id, name: 'Research' })?.title ?? null,
    null,
    'a name does not make an idle desktop busy',
  );

  const asking = normalizeAgentDesktop(id, { ...summary, needsYou: 2, queued: 1 }, { name: 'Research' });
  assert.equal(asking.title, 'Research');
  assert.equal(asking.status, 'waiting');
  assert.equal(asking.needsAttention, true);
  assert.equal(asking.subtitle, '2 to answer · 1 waiting its turn');
  // Recorded gap: the attention summary carries no timestamps at all.
  assert.equal(asking.startedAt, null);
  assert.equal(asking.updatedAt, null);
});

test('a server with no update to install and none available reports nothing', () => {
  assert.deepEqual(UPDATE_JOB, {
    state: 'idle',
    percent: 0,
    message: '',
    version: null,
    rollback: false,
  });
  assert.equal(UPDATES.available, false);
  assert.equal(normalizeUpdate(UPDATES, UPDATE_JOB), null);
});

test('an available update asks for a person; one being installed shows progress', () => {
  const available = normalizeUpdate(
    { ...UPDATES, available: true, latest: '0.2.0', checkedAt: '2026-09-18T04:00:00' },
    UPDATE_JOB,
  );
  assertShape(available);
  assert.equal(available.kind, 'update');
  assert.equal(available.title, 'Vela 0.2.0 is available');
  assert.equal(available.subtitle, 'You are running 0.1.15');
  assert.equal(available.status, 'waiting');
  assert.equal(available.needsAttention, true);
  assert.equal(available.href, '/settings#updates');

  const running = normalizeUpdate(UPDATES, {
    ...UPDATE_JOB,
    state: 'downloading',
    percent: 42,
    message: 'Downloading Vela 0.2.0…',
    version: '0.2.0',
  });
  assert.equal(running.status, 'running');
  assert.equal(running.title, 'Installing Vela 0.2.0');
  assert.deepEqual(running.progress, { completed: 42, total: 100, percent: 42 });
  assert.equal(running.needsAttention, false);

  const failed = normalizeUpdate(UPDATES, {
    ...UPDATE_JOB,
    state: 'error',
    message: 'The download did not verify.',
    version: '0.2.0',
  });
  assert.equal(failed.status, 'failed');
  assert.equal(failed.needsAttention, true);
});

test('a backup is finished work, named by when it was taken', () => {
  const backup = BACKUPS.backups[0];
  const operation = normalizeBackup(backup);
  assertShape(operation);
  assert.equal(operation.kind, 'backup');
  assert.equal(operation.title, 'Backup');
  assert.equal(operation.subtitle, backup.name);
  assert.equal(operation.status, 'done');
  assert.equal(operation.needsAttention, false);
  assert.equal(operation.startedAt, backup.created_at);
  assert.equal(normalizeBackup({ ...backup, safety: true }).title, 'Safety copy before a restore');
});

test('the health sweep is one operation, and a failed check asks for a person', () => {
  const operation = normalizeDoctor(DOCTOR);
  assertShape(operation);
  assert.equal(operation.kind, 'doctor');
  assert.equal(operation.status, 'done');
  assert.equal(operation.needsAttention, false);
  assert.equal(operation.subtitle, `${DOCTOR.checks.length} checks passed`);
  assert.equal(operation.progress.total, DOCTOR.checks.length);
  assert.equal(operation.progress.percent, 100);

  const broken = {
    checks: DOCTOR.checks.map((check, index) => (index === 0 ? { ...check, status: 'fail' } : check)),
  };
  const failing = normalizeDoctor(broken);
  assert.equal(failing.status, 'failed');
  assert.equal(failing.needsAttention, true);
  assert.equal(failing.subtitle, '1 check needs attention');
  assert.equal(normalizeDoctor({ checks: [] }), null, 'a sweep that never ran is not an operation');
});

test('an install in progress is listed, with the timestamps the engine does not send', () => {
  const app = APPS.apps.find((entry) => !entry.installed) || APPS.apps[0];
  const operation = normalizeInstall(app);
  assertShape(operation);
  assert.equal(operation.kind, 'install');
  assert.equal(operation.key, `install:${app.id}`);
  assert.equal(operation.title, `Installing ${app.name}`);
  assert.equal(operation.status, 'running');
  // Recorded gap: no endpoint reports an install in progress.
  assert.equal(operation.startedAt, null);
  assert.equal(operation.updatedAt, null);
});

test('every source merges into one list, attention first and newest next', () => {
  const merged = normalizeOperations({
    runs: RUNS,
    attention: { desktops: { d1: { needsYou: 1, queued: 0, working: false, blocked: null } } },
    desktops: [{ id: 'd1', name: 'Research' }],
    updates: UPDATES,
    updateJob: UPDATE_JOB,
    backups: BACKUPS,
    doctor: DOCTOR,
    installing: [APPS.apps[0]],
  });
  merged.forEach(assertShape);
  assert.equal(merged[0].kind, 'agent-task', 'the desktop that needs a person comes first');
  assert.equal(merged[0].needsAttention, true);
  assert.equal(merged.at(-1).kind, 'install', 'the one with no timestamp comes last');
  assert.deepEqual(
    [...merged].sort((a, b) => a.kind.localeCompare(b.kind)).map((item) => item.kind),
    ['agent-task', 'automation-run', 'backup', 'doctor', 'install'],
    'every source is represented once',
  );
  // The backup and the sweep really did happen after the run on the machine
  // that captured these; the order below is the fixtures', not a guess.
  const times = merged.slice(1, -1).map((item) => Date.parse(item.updatedAt));
  assert.deepEqual(times, [...times].sort((a, b) => b - a), 'newest first');
  assert.equal(new Set(merged.map((item) => item.key)).size, merged.length, 'keys are unique');
});

test('an empty server produces an empty list rather than a row about nothing', () => {
  assert.deepEqual(normalizeOperations(), []);
  assert.deepEqual(
    normalizeOperations({
      runs: { runs: [] },
      attention: { desktops: {} },
      updates: UPDATES,
      updateJob: UPDATE_JOB,
      backups: { backups: [] },
      doctor: { checks: [] },
      installing: [],
    }),
    [],
  );
});

test('sorting puts attention first, then the most recently touched', () => {
  const at = (updatedAt, needsAttention = false) => ({ updatedAt, needsAttention, key: updatedAt });
  assert.deepEqual(
    sortOperations([
      at('2026-09-01T00:00:00'),
      at('2026-09-03T00:00:00'),
      at(null),
      at('2026-09-02T00:00:00', true),
    ]).map((item) => item.key),
    ['2026-09-02T00:00:00', '2026-09-03T00:00:00', '2026-09-01T00:00:00', null],
  );
});

test('the vocabulary is one table, and an engine spelling still reads right', () => {
  assert.deepEqual(STATUSES, [
    'queued',
    'running',
    'waiting',
    'done',
    'failed',
    'cancelled',
    'interrupted',
  ]);
  assert.deepEqual(
    STATUSES.map(statusTone),
    ['neutral', 'active', 'warn', 'good', 'bad', 'neutral', 'warn'],
  );
  assert.deepEqual(STATUSES.map(statusDotState), ['off', 'ok', 'bad', 'ok', 'bad', 'off', 'bad']);

  // The wording every surface used before this table existed.
  assert.equal(statusLabel('queued'), 'Waiting to start');
  assert.equal(statusLabel('running'), 'Running');
  assert.equal(statusLabel('waiting'), 'Waiting for you');
  assert.equal(statusLabel('succeeded'), 'Finished');
  assert.equal(statusLabel('failed'), 'Failed');
  assert.equal(statusLabel('cancelled'), 'Cancelled');
  assert.equal(statusLabel('interrupted'), 'Interrupted');
  assert.equal(statusLabel('timed_out'), 'Took too long');

  assert.equal(operationStatus('succeeded'), 'done');
  assert.equal(operationStatus('timed_out'), 'failed');
  assert.equal(operationStatus('nonsense'), 'queued', 'an unknown status has not started');
  assert.equal(statusTone('timed_out'), 'bad');
});

test('agent task states keep their own words and still map to the seven', () => {
  assert.equal(agentStateLabel('waiting_approval'), 'Needs you');
  assert.equal(agentStateLabel('human_control'), 'Yours');
  assert.equal(agentStateLabel('outcome_unknown'), 'Outcome unknown');
  assert.equal(agentStateStatus('waiting_approval'), 'waiting');
  assert.equal(agentStateStatus('human_control'), 'waiting');
  assert.equal(agentStateStatus('taking_over'), 'waiting');
  assert.equal(agentStateStatus('outcome_unknown'), 'interrupted');
  assert.equal(agentStateStatus('starting'), 'running');
  assert.equal(agentStateStatus('succeeded'), 'done');
});
