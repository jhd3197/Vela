// One desktop's saved boards, and the one place that writes them.
//
// `revision` is optimistic concurrency, not a version number the user sees: the
// server rejects a save that was built on an older board (409), and the honest
// answer to that is to reload what is really stored and say so, rather than
// overwrite an arrangement this tab never saw.
//
// Every call names the desktop it is for. Boards belong to a workspace, and a
// save that forgot which one would land on whichever desktop came first.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { desktopsApi } from '../desktops/desktopsApi.js';
import { defaultBoards, repairBoards } from './boards.js';

export const deskApi = {
  load: (desktopId) => desktopsApi.boards(desktopId),
  save: (desktopId, revision, boards) => desktopsApi.saveBoards(desktopId, revision, boards),
};

const idle = { boards: defaultBoards(), revision: null, error: null, loaded: false };

export default function useDeskBoards(knownTypes, desktopId) {
  // `loaded` is false until this desktop's own board has arrived. The seeded
  // boards are a shape for the code between mount and answer, not something to
  // draw: the page waits rather than showing a default it would then rearrange.
  const [state, setState] = useState(idle);
  const typesRef = useRef(knownTypes);
  typesRef.current = knownTypes;

  const apply = useCallback((payload) => {
    const boards = repairBoards(payload.boards, typesRef.current);
    setState({ boards, revision: payload.revision, error: null, loaded: true });
    return boards;
  }, []);

  useEffect(() => {
    if (!desktopId) {
      // Between "the page mounted" and "we know which desktop this is", the
      // desk shows the seed and cannot be saved. Marking it unloaded rather
      // than loaded-and-empty is what stops a save landing nowhere.
      setState(idle);
      return undefined;
    }
    let live = true;
    // Switching desktops must not leave the previous one's widgets on screen
    // while the next answers, so the board resets first.
    setState(idle);
    deskApi
      .load(desktopId)
      .then((payload) => {
        if (live) apply(payload);
      })
      .catch((error) => {
        // A desk that cannot be loaded still shows the seeded board; it just
        // cannot be saved, which `revision: null` says to the caller.
        if (live) setState((previous) => ({ ...previous, error, loaded: true }));
      });
    return () => {
      live = false;
    };
  }, [apply, desktopId]);

  /**
   * Store both boards. Returns `{ ok: true }`, `{ ok: false, conflict: true,
   * boards }` after reloading what is really there, or `{ ok: false, message }`.
   */
  const save = useCallback(
    async (boards) => {
      if (!desktopId) return { ok: false, message: 'No desktop is selected yet.' };
      if (state.revision === null) return { ok: false, message: 'The desk is not loaded yet.' };
      try {
        const payload = await deskApi.save(desktopId, state.revision, boards);
        apply(payload);
        return { ok: true };
      } catch (error) {
        if (error.status === 409) {
          const fresh = await deskApi.load(desktopId).catch(() => null);
          // The caller has a draft built on the board that just lost; hand it
          // what is really stored so it can reset to that rather than guess.
          return { ok: false, conflict: true, boards: fresh ? apply(fresh) : null };
        }
        return { ok: false, message: error.message || 'Could not save the desk.' };
      }
    },
    [desktopId, state.revision, apply],
  );

  return useMemo(() => ({ ...state, save }), [state, save]);
}
