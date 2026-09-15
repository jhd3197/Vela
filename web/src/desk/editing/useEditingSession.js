// React binding for the Arrange-mode transaction store.
//
// Origin: ServerKit `frontend/src/hooks/useEditingSession.js` (MIT, same
// owner), unchanged apart from formatting and the import path.
import { useCallback, useReducer, useRef } from 'react';

import { createEditingSession, editingSessionReducer } from './editingSession.js';

export default function useEditingSession({ baseline, historyLimit = 50 }) {
  const [state, dispatch] = useReducer(
    editingSessionReducer,
    { baseline, historyLimit },
    (initial) => createEditingSession(initial.baseline, { historyLimit: initial.historyLimit }),
  );
  const stateRef = useRef(state);
  stateRef.current = state;

  const change = useCallback((path, value, options = {}) => {
    dispatch({ type: 'change', path, value, ...options });
  }, []);
  const transaction = useCallback((next, options = {}) => {
    const payload = typeof next === 'function' ? { update: next } : { draft: next };
    dispatch({ type: 'transaction', ...payload, ...options });
  }, []);
  const undo = useCallback(() => dispatch({ type: 'undo' }), []);
  const redo = useCallback(() => dispatch({ type: 'redo' }), []);
  const reset = useCallback((nextBaseline) => {
    dispatch(
      nextBaseline === undefined ? { type: 'reset' } : { type: 'reset', baseline: nextBaseline },
    );
  }, []);
  const save = useCallback(async (saveDraft) => {
    const draft = stateRef.current.draft;
    dispatch({ type: 'saveStarted' });
    try {
      const result = await saveDraft(draft);
      dispatch({ type: 'saveSucceeded', baseline: result === undefined ? draft : result });
      return result;
    } catch (error) {
      dispatch({ type: 'saveFailed', error });
      throw error;
    }
  }, []);

  return { ...state, change, transaction, undo, redo, reset, save, dispatch };
}
