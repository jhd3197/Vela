// Undo/redo transaction store for Arrange mode.
//
// Origin: ServerKit `frontend/src/hooks/editingSession.js` (MIT, same owner).
// Copied as-is apart from formatting; it is a pure reducer with no imports.

const DEFAULT_HISTORY_LIMIT = 50;

const clone = (value) => {
  if (typeof structuredClone === 'function') return structuredClone(value);
  return JSON.parse(JSON.stringify(value));
};

const isObject = (value) => value !== null && typeof value === 'object';

const equal = (left, right) => {
  if (Object.is(left, right)) return true;
  if (!isObject(left) || !isObject(right)) return false;
  if (Array.isArray(left) !== Array.isArray(right)) return false;
  const leftKeys = Object.keys(left).sort();
  const rightKeys = Object.keys(right).sort();
  if (leftKeys.length !== rightKeys.length) return false;
  return leftKeys.every((key, index) => key === rightKeys[index] && equal(left[key], right[key]));
};

const pathParts = (path) =>
  Array.isArray(path) ? path.map(String) : String(path).split('.').filter(Boolean);

const setAtPath = (source, path, value) => {
  const parts = pathParts(path);
  if (!parts.length) return clone(value);
  const next = clone(source);
  let cursor = next;
  parts.forEach((part, index) => {
    if (index === parts.length - 1) {
      cursor[part] = clone(value);
      return;
    }
    const nextPart = parts[index + 1];
    if (!isObject(cursor[part])) cursor[part] = /^\d+$/.test(nextPart) ? [] : {};
    cursor = cursor[part];
  });
  return next;
};

const changedPaths = (baseline, draft, prefix = '') => {
  if (equal(baseline, draft)) return [];
  if (!isObject(baseline) || !isObject(draft) || Array.isArray(baseline) !== Array.isArray(draft)) {
    return [prefix || '$'];
  }
  const keys = [...new Set([...Object.keys(baseline), ...Object.keys(draft)])].sort();
  return keys.flatMap((key) =>
    changedPaths(baseline[key], draft[key], prefix ? `${prefix}.${key}` : key),
  );
};

const derive = (state) => {
  const dirtyPaths = changedPaths(state.baseline, state.draft);
  return {
    ...state,
    dirtyPaths,
    isDirty: dirtyPaths.length > 0,
    canUndo: state.past.length > 0,
    canRedo: state.future.length > 0,
  };
};

export function createEditingSession(baseline, { historyLimit = DEFAULT_HISTORY_LIMIT } = {}) {
  const initial = clone(baseline);
  return derive({
    baseline: initial,
    draft: clone(initial),
    past: [],
    future: [],
    historyLimit,
    saveState: 'idle',
    error: null,
  });
}

const commitDraft = (state, nextDraft, coalesceKey = null) => {
  if (equal(state.draft, nextDraft)) return state;
  const last = state.past[state.past.length - 1];
  const coalesces = Boolean(coalesceKey && last?.coalesceKey === coalesceKey);
  const past = coalesces
    ? state.past
    : [...state.past, { draft: clone(state.draft), coalesceKey }].slice(-state.historyLimit);
  return derive({
    ...state,
    draft: clone(nextDraft),
    past,
    future: [],
    saveState: 'idle',
    error: null,
  });
};

const transactionDraft = (state, action) => {
  if (Object.hasOwn(action, 'draft')) return action.draft;
  if (typeof action.update === 'function') return action.update(clone(state.draft));
  return (action.changes || []).reduce(
    (draft, change) => setAtPath(draft, change.path, change.value),
    state.draft,
  );
};

export function editingSessionReducer(state, action) {
  switch (action.type) {
    case 'change':
      return commitDraft(
        state,
        setAtPath(state.draft, action.path, action.value),
        action.coalesceKey,
      );
    case 'transaction':
      return commitDraft(state, transactionDraft(state, action), action.coalesceKey);
    case 'undo': {
      // The current draft moves to the front of redo history.
      if (!state.past.length) return state;
      const previous = state.past[state.past.length - 1];
      return derive({
        ...state,
        draft: clone(previous.draft),
        past: state.past.slice(0, -1),
        future: [{ draft: clone(state.draft) }, ...state.future],
        saveState: 'idle',
        error: null,
      });
    }
    case 'redo': {
      if (!state.future.length) return state;
      const [next, ...future] = state.future;
      return derive({
        ...state,
        draft: clone(next.draft),
        past: [...state.past, { draft: clone(state.draft), coalesceKey: null }].slice(
          -state.historyLimit,
        ),
        future,
        saveState: 'idle',
        error: null,
      });
    }
    case 'reset':
      return createEditingSession(
        Object.hasOwn(action, 'baseline') ? action.baseline : state.baseline,
        { historyLimit: state.historyLimit },
      );
    case 'saveStarted':
      return { ...state, saveState: 'saving', error: null };
    case 'saveSucceeded': {
      const baseline = clone(Object.hasOwn(action, 'baseline') ? action.baseline : state.draft);
      return derive({
        ...state,
        baseline,
        draft: clone(baseline),
        past: [],
        future: [],
        saveState: 'saved',
        error: null,
      });
    }
    case 'saveFailed':
      return { ...state, saveState: 'error', error: action.error || null };
    default:
      return state;
  }
}
