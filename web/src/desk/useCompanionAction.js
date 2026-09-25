import { useCallback, useState } from 'react';
import { api } from '../api.js';
import { useApps } from '../store.jsx';
import { useConfirm } from '../hooks/useConfirm.js';

// Pressing one of a companion app's buttons.
//
// A companion is a program on the Vela computer the owner connected, and
// connecting it was the review of what its buttons do. So unlike an SDK app's
// widget, where a button only opens the app, this asks the program to do it —
// after its own confirmation question, when it declared one.
export default function useCompanionAction(onDone) {
  const { pushToast } = useApps();
  const confirm = useConfirm();
  const [running, setRunning] = useState(null);

  const run = useCallback(
    async (app, actionId) => {
      const declared = (app?.companion?.actions || []).find((item) => item.id === actionId);
      if (!declared) return;
      if (declared.confirm) {
        const sure = await confirm({ title: declared.confirm, confirmText: declared.title });
        if (!sure) return;
      }
      setRunning(actionId);
      try {
        const result = await api.runCompanionAction(app.id, actionId);
        pushToast?.(result?.message || `${declared.title}: done`);
        window.dispatchEvent(new Event('vela:widgets-changed'));
        onDone?.();
      } catch (error) {
        pushToast?.(error.message || `${declared.title} failed`, 'error');
      } finally {
        setRunning(null);
      }
    },
    [confirm, onDone, pushToast],
  );

  return { run, running };
}
