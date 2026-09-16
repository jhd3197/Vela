// Severity detection and match highlighting, from ServerKit's
// `log-viewer/logHelpers.js` (MIT, same owner). ServerKit groups nginx, MySQL
// and syslog files; Vela only ever shows the logs it wrote itself, so the
// grouping here is Vela's own kinds and the severity patterns are kept.

const SEVERITY_PATTERNS = [
  { id: 'fatal', re: /\b(FATAL|CRITICAL|EMERG|ALERT|PANIC)\b/i },
  { id: 'error', re: /\b(ERROR|ERR|FAIL(?:ED)?|EXCEPTION|TRACEBACK)\b/i },
  { id: 'warn', re: /\b(WARN(?:ING)?|DEPRECATED)\b/i },
  { id: 'info', re: /\b(INFO|NOTICE|STARTING|STARTED|READY|LISTENING)\b/i },
  { id: 'debug', re: /\b(DEBUG|TRACE|VERBOSE)\b/i },
];

// Order matters: fatal and error before warn, warn before info.
export function severityOf(line) {
  if (!line) return null;
  for (const pattern of SEVERITY_PATTERNS) {
    if (pattern.re.test(line)) return pattern.id;
  }
  return null;
}

function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// Segments of a line as [{ text, match }], so the viewer can mark the search
// hits without putting caller text through `dangerouslySetInnerHTML`.
export function splitOnMatch(text, query) {
  if (!query) return [{ text, match: false }];
  // A `/…/` search is a regular expression on the server. Highlighting it
  // exactly would mean re-running it here; showing the line unmarked is
  // honest, where a wrong highlight would not be.
  if (query.length > 2 && query.startsWith('/') && query.endsWith('/')) {
    return [{ text, match: false }];
  }
  const parts = text.split(new RegExp(`(${escapeRegex(query)})`, 'gi'));
  return parts
    .filter((part) => part !== '')
    .map((part, index) => ({ text: part, match: index % 2 === 1 }));
}

export const KIND_LABELS = {
  server: 'Server',
  audit: 'Activity',
  app: 'Apps',
  worker: 'Automations',
};

export function kindLabel(kind) {
  return KIND_LABELS[kind] || 'Other';
}

// The file list is grouped by kind in this order, with rotated files folded
// under the log they came from.
export const KIND_ORDER = ['server', 'audit', 'worker', 'app'];

export function groupLogs(logs) {
  const groups = new Map();
  for (const log of logs) {
    if (!groups.has(log.kind)) groups.set(log.kind, []);
    groups.get(log.kind).push(log);
  }
  return [...groups.entries()]
    .sort((a, b) => {
      const left = KIND_ORDER.indexOf(a[0]);
      const right = KIND_ORDER.indexOf(b[0]);
      return (left < 0 ? KIND_ORDER.length : left) - (right < 0 ? KIND_ORDER.length : right);
    })
    .map(([kind, entries]) => ({ kind, label: kindLabel(kind), entries }));
}

export const LINE_CHOICES = [50, 100, 200, 500, 1000];
