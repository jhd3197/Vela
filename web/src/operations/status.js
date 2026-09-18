// The one status vocabulary for background work.
//
// Origin: ServerKit `frontend/src/components/ds/status.js` (MIT, same owner).
// Before this, an automation run's status was spelled out in
// `automationsApi.js`, an agent task's in `AgentWindow.jsx`, and the tone came
// from whichever table the page happened to import — so the same run could
// read "Waiting for you" on one surface and "waiting" on another, and a status
// the engine added reached some of them and not the rest.
//
// There are seven statuses an operation can be in. The engine spells some of
// them differently, and one of those spellings carries a distinction worth
// keeping in words ("Took too long" rather than a flat "Failed"), so the
// tables below accept an engine status as well as a canonical one.
//
// Tones are Vela's, not ServerKit's: `good`, `bad`, `warn`, `active` and
// `neutral` are what `run-status-*` and `run-tone-*` already style. Adding a
// palette of new names would mean new classes with no rules behind them.

/** The statuses an operation can be in, in the order they read as a life. */
export const STATUSES = [
  'queued',
  'running',
  'waiting',
  'done',
  'failed',
  'cancelled',
  'interrupted',
];

// Engine spelling → the status the dashboard reasons about.
const CANONICAL = {
  succeeded: 'done',
  complete: 'done',
  completed: 'done',
  ok: 'done',
  timed_out: 'failed',
  error: 'failed',
  fail: 'failed',
  canceled: 'cancelled',
  pending: 'queued',
  starting: 'running',
  working: 'running',
  waiting_approval: 'waiting',
  paused: 'waiting',
};

const TONES = {
  queued: 'neutral',
  running: 'active',
  waiting: 'warn',
  done: 'good',
  failed: 'bad',
  cancelled: 'neutral',
  interrupted: 'warn',
};

// Keyed by what is being described, so an engine spelling that says more than
// its canonical status keeps saying it.
const LABELS = {
  queued: 'Waiting to start',
  running: 'Running',
  waiting: 'Waiting for you',
  done: 'Finished',
  succeeded: 'Finished',
  failed: 'Failed',
  timed_out: 'Took too long',
  cancelled: 'Cancelled',
  interrupted: 'Interrupted',
};

// The desk's status dot has three states of its own. `warn` has no rule in the
// stylesheet, so an amber row would draw green; it reads as bad instead, which
// is the honest half of the pair.
const DOTS = {
  queued: 'off',
  running: 'ok',
  waiting: 'bad',
  done: 'ok',
  failed: 'bad',
  cancelled: 'off',
  interrupted: 'bad',
};

const key = (status) =>
  String(status ?? '')
    .toLowerCase()
    .trim();

/** An engine status in the seven words the dashboard reasons about. */
export function operationStatus(status) {
  const raw = key(status);
  if (STATUSES.includes(raw)) return raw;
  return CANONICAL[raw] || 'queued';
}

/** The tone class suffix: `run-tone-${statusTone(status)}`. */
export function statusTone(status) {
  return TONES[operationStatus(status)] || 'neutral';
}

/** What a person is told this status is. */
export function statusLabel(status) {
  const raw = key(status);
  return LABELS[raw] || LABELS[operationStatus(raw)] || 'Waiting to start';
}

/** The desk status dot's `data-state`. */
export function statusDotState(status) {
  return DOTS[operationStatus(status)] || 'off';
}

// ---------------------------------------------------------------------------
// Agent tasks have a life of their own — handing control to a person and back
// is a state a run does not have — so their words live here beside the rest of
// the vocabulary rather than in the window that shows them.
// ---------------------------------------------------------------------------

const AGENT_WORDS = {
  queued: 'Waiting its turn',
  starting: 'Starting',
  running: 'Working',
  waiting_approval: 'Needs you',
  paused: 'Paused',
  taking_over: 'Handing over',
  human_control: 'Yours',
  succeeded: 'Done',
  failed: 'Stopped',
  cancelled: 'Stopped by you',
  interrupted: 'Interrupted',
  outcome_unknown: 'Outcome unknown',
};

/** An agent task's state in the words the rest of the interface uses. */
export function agentStateLabel(state) {
  return AGENT_WORDS[key(state)] || state;
}

/** An agent task's state as one of the seven. */
export function agentStateStatus(state) {
  const raw = key(state);
  if (raw === 'taking_over' || raw === 'human_control') return 'waiting';
  if (raw === 'outcome_unknown') return 'interrupted';
  return operationStatus(raw);
}
