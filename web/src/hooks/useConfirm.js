import { createContext, useContext } from 'react';

// "Are you sure?" asked once, app-wide.
//
// Origin: ServerKit `frontend/src/hooks/useConfirm.js` and its context (MIT,
// same owner). `ConfirmProvider` renders the one dialog; this is how a page
// reaches it.
//
//   const confirm = useConfirm();
//   if (!(await confirm({ title: 'Delete this?', message: '…' }))) return;
//
// Pass `onConfirm` when the work should happen inside the dialog: it then
// shows its pending state, keeps itself open if the work fails, and says what
// went wrong. Without it the dialog closes as soon as the answer is given.
export const ConfirmContext = createContext(null);

// No provider: pre-auth screens and test fixtures mount fragments of the tree.
// Resolving `true` keeps the action working; the warning says why nobody was
// asked.
function unattached(options) {
  console.warn('useConfirm() outside a ConfirmProvider — proceeding without asking.', options);
  return Promise.resolve(options?.onConfirm ? Promise.resolve(options.onConfirm()) : true).then(
    () => true,
  );
}

export function useConfirm() {
  return useContext(ConfirmContext) || unattached;
}

export default useConfirm;
