import { useCallback, useId, useMemo, useRef, useState } from 'react';
import { ConfirmContext } from '../hooks/useConfirm.js';
import Button from './ui/Button.jsx';
import Dialog from './ui/Dialog.jsx';

// The one confirmation dialog for the whole dashboard.
//
// It is built on `ui/Dialog`, which already owns focus containment, returning
// focus to whatever opened it, Escape, and refusing to dismiss while pending —
// so this only has to decide what the dialog says and what the answer means.
// Cancel takes the initial focus: it is the safe half of the question.
export default function ConfirmProvider({ children }) {
  const [request, setRequest] = useState(null);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState('');
  const cancelRef = useRef(null);
  const titleId = useId();

  const confirm = useCallback((options = {}) => {
    setFailure('');
    setPending(false);
    return new Promise((resolve) => {
      setRequest({
        title: 'Are you sure?',
        message: '',
        confirmText: 'Confirm',
        cancelText: 'Cancel',
        variant: 'danger',
        ...options,
        resolve,
      });
    });
  }, []);

  const value = useMemo(() => confirm, [confirm]);

  const settle = (answer) => {
    const pendingRequest = request;
    setRequest(null);
    setPending(false);
    setFailure('');
    pendingRequest?.resolve(answer);
  };

  const accept = async () => {
    if (!request.onConfirm) {
      settle(true);
      return;
    }
    // The caller asked for the work to happen here, so the dialog stays up and
    // says what it is doing. A failure keeps it open with the reason, which is
    // the behaviour each of these dialogs had before there was one of them.
    setPending(true);
    setFailure('');
    try {
      await request.onConfirm();
      settle(true);
    } catch (error) {
      setPending(false);
      setFailure(error?.message || 'That did not work. Try again.');
    }
  };

  return (
    <ConfirmContext.Provider value={value}>
      {children}
      {request && (
        <Dialog
          open
          pending={pending}
          initialFocusRef={cancelRef}
          aria-labelledby={titleId}
          onClose={() => settle(false)}
        >
          <h2 id={titleId}>{request.title}</h2>
          {request.message && <p>{request.message}</p>}
          {failure && (
            <p className="inline-error" role="alert">
              {failure}
            </p>
          )}
          <div className="form-actions">
            <Button ref={cancelRef} onClick={() => settle(false)} disabled={pending}>
              {request.cancelText}
            </Button>
            <Button variant={request.variant} pending={pending} onClick={accept}>
              {pending ? request.pendingText || 'Working…' : request.confirmText}
            </Button>
          </div>
        </Dialog>
      )}
    </ConfirmContext.Provider>
  );
}
