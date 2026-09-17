// What the agent is doing, in plain language.
//
// Deliberately not a log. There is no hidden reasoning here, no percentage
// invented from a step count, and no claim about how far along anything is:
// a task does not know how many steps it needs, and a bar that pretended
// otherwise would be a bar that lies.
//
// Each line is one thing that happened and whether it worked. Refusals are
// shown with their reason, because a run being told "that control is gone" is
// the interesting part of what happened, not something to hide.
const WORDS = {
  'task.queued': () => 'Waiting its turn',
  'task.starting': () => 'Starting',
  'task.running': (payload) => `Working, using ${payload.model || 'the model'}`,
  'task.paused': () => 'Paused',
  'task.resumed': () => 'Carrying on',
  'task.succeeded': () => 'Finished',
  'task.failed': (payload) => payload.detail || 'Stopped',
  'task.cancelled': () => 'Stopped by you',
  'task.interrupted': () => 'Interrupted',
  'task.attention': (payload) => ATTENTION[payload.reason] || payload.detail || 'This needs you',
  'task.file': (payload) => `Got a file: ${payload.name}`,
  'approval.waiting': (payload) => payload.summary?.headline || 'Waiting for you',
  'approval.resolved': (payload) =>
    payload.state === 'approved' ? 'You allowed it' : `Not allowed: ${payload.state}`,
  'step.started': (payload) => TOOLS[payload.tool] || payload.tool,
  // A step that worked is already covered by the line that started it. One that
  // was refused is the interesting part, so that is the one that gets a line of
  // its own with its reason.
  'step.finished': (payload) =>
    payload.ok === false ? `That did not work: ${TOOLS[payload.tool] || payload.tool}` : null,
};

/** Why a task handed itself back. Named rather than free text, so the reason
 *  can be acted on rather than read and guessed at. */
const ATTENTION = {
  login: 'Waiting for you to sign in',
  challenge: 'The site asked something only a person can answer',
  confirm: 'Waiting for you to confirm something on the site',
  blocked: 'Stopped: this needs you at the keyboard',
};

/** One tool, as a person would describe it. */
const TOOLS = {
  'desktop.observe': 'Looking at the window',
  'desktop.open_app': 'Opening an app',
  'desktop.open_site': 'Opening a website',
  'desktop.select_view': 'Switching window',
  'desktop.click': 'Clicking',
  'desktop.type': 'Typing',
  'desktop.keypress': 'Pressing a key',
  'desktop.scroll': 'Scrolling',
  'desktop.wait': 'Waiting for the page',
  'desktop.attach_file': 'Attaching a file',
  'task.needs_person': 'Asking you to take over',
  'app.invoke_action': 'Asking the app to do something',
  'task.finish': 'Wrapping up',
};

export function describe(event) {
  const say = WORDS[event.kind];
  return say ? say(event.payload || {}) : null;
}

/** The states a task can still move out of. */
export const LIVE_STATES = [
  'queued',
  'starting',
  'running',
  'waiting_approval',
  'paused',
  'taking_over',
  'human_control',
];

export default function TaskActivity({ events, gap, onClearGap }) {
  const lines = events
    .map((event) => ({ event, text: describe(event) }))
    .filter((entry) => entry.text)
    .slice(-40);

  return (
    <div className="task-activity">
      {gap && (
        <p className="task-gap" role="status">
          Some of this task&apos;s activity is no longer available — the list below starts part-way
          through. What the task has done and where it got to are still correct.
          <button type="button" onClick={onClearGap}>
            Got it
          </button>
        </p>
      )}
      <ol aria-live="polite" aria-relevant="additions">
        {lines.map(({ event, text }) => {
          const failed = event.kind === 'step.finished' && event.payload?.ok === false;
          const attention = event.kind === 'task.attention';
          return (
            <li
              key={`${event.desktopId}-${event.sequence}`}
              data-state={attention ? 'attention' : failed ? 'refused' : null}
            >
              <span>{text}</span>
              {event.payload?.detail && (attention || (event.kind !== 'task.failed' && failed)) ? (
                <small>{event.payload.detail}</small>
              ) : null}
            </li>
          );
        })}
      </ol>
      {!lines.length && <p className="field-hint">Nothing has happened yet.</p>}
    </div>
  );
}
