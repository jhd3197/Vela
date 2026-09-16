// The saved boards, and the one place that writes them.
//
// `revision` is optimistic concurrency, not a version number the user sees: the
// server rejects a save that was built on an older board (409), and the honest
// answer to that is to reload what is really stored and say so, rather than
// overwrite an arrangement this tab never saw.
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, hubFetch } from '../api.js';
import { defaultBoards, repairBoards } from './boards.js';

async function read(path, options = {}) {
  let response;
  try {
    response = await hubFetch(path, { headers: { Accept: 'application/json' }, ...options });
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError('Cannot reach the Vela backend.', 0);
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body && typeof body.detail === 'string') detail = body.detail;
    } catch {
      // Keep the status-based message when the body is not JSON.
    }
    throw new ApiError(detail, response.status);
  }
  return response.json();
}

export const deskApi = {
  load: () => read('/api/desk'),
  save: (revision, boards) =>
    read('/api/desk', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ revision, boards }),
    }),
};

export default function useDeskBoards(knownTypes) {
  // The seeded desk is what the user sees for the moment before the server
  // answers. It is the same seed the server would send, so the board does not
  // visibly rearrange itself on load.
  const [state, setState] = useState(() => ({
    boards: defaultBoards(),
    revision: null,
    error: null,
    loaded: false,
  }));
  const typesRef = useRef(knownTypes);
  typesRef.current = knownTypes;

  const apply = useCallback((payload) => {
    const boards = repairBoards(payload.boards, typesRef.current);
    setState({ boards, revision: payload.revision, error: null, loaded: true });
    return boards;
  }, []);

  useEffect(() => {
    let live = true;
    deskApi
      .load()
      .then((payload) => {
        if (live) apply(payload);
      })
      .catch((error) => {
        // A desk that cannot be loaded still shows the seeded board; it just
        // cannot be saved, which `revision: null` says to the caller.
        if (live) setState((prev) => ({ ...prev, error, loaded: true }));
      });
    return () => {
      live = false;
    };
  }, [apply]);

  /**
   * Store both boards. Returns `{ ok: true }`, `{ ok: false, conflict: true,
   * boards }` after reloading what is really there, or `{ ok: false, message }`.
   */
  const save = useCallback(
    async (boards) => {
      if (state.revision === null) return { ok: false, message: 'The desk is not loaded yet.' };
      try {
        const payload = await deskApi.save(state.revision, boards);
        apply(payload);
        return { ok: true };
      } catch (error) {
        if (error.status === 409) {
          const fresh = await deskApi.load().catch(() => null);
          // The caller has a draft built on the board that just lost; hand it
          // what is really stored so it can reset to that rather than guess.
          return { ok: false, conflict: true, boards: fresh ? apply(fresh) : null };
        }
        return { ok: false, message: error.message || 'Could not save the desk.' };
      }
    },
    [state.revision, apply],
  );

  return { ...state, save };
}
