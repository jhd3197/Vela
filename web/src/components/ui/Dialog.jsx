import { useLayoutEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

// A second modal must not unlock page scrolling while the first remains open.
const scrollLocks = new WeakMap();
function lockScroll(document) {
  let lock = scrollLocks.get(document);
  if (!lock) {
    lock = { count: 0, overflow: document.documentElement.style.overflow };
    scrollLocks.set(document, lock);
    document.documentElement.style.overflow = 'hidden';
  }
  lock.count++;
  return () => {
    if (--lock.count === 0) {
      document.documentElement.style.overflow = lock.overflow;
      scrollLocks.delete(document);
    }
  };
}

// Controlled open state. The browser owns focus containment and background
// inertness; callers own confirmations, actions, and the decision to close.
export default function Dialog({
  open,
  onClose,
  pending = false,
  closeOnBackdrop = false,
  initialFocusRef,
  returnFocusRef,
  contentRef,
  className = 'modal-dialog',
  children,
  ...props
}) {
  const dialogRef = useRef(null);
  const pointerStartedOutside = useRef(false);

  useLayoutEffect(() => {
    if (!open) return undefined;
    const dialog = dialogRef.current;
    const document = dialog.ownerDocument;
    const opener = document.activeElement;
    const fallbackFocus = returnFocusRef?.current;
    const unlock = lockScroll(document);
    dialog.showModal();
    // A caller can choose the safest initial action (usually Cancel/Close).
    initialFocusRef?.current?.focus({ preventScroll: true });
    return () => {
      dialog.close();
      unlock();
      const returnTo = opener?.isConnected ? opener : fallbackFocus;
      if (returnTo?.isConnected && typeof returnTo.focus === 'function') {
        returnTo.focus({ preventScroll: true });
      }
    };
  }, [open, initialFocusRef, returnFocusRef]);

  const requestClose = () => {
    if (!pending) onClose();
  };
  const isOutside = (event) => {
    const panel = contentRef?.current || dialogRef.current;
    const box = panel.getBoundingClientRect();
    return (
      event.clientX < box.left ||
      event.clientX > box.right ||
      event.clientY < box.top ||
      event.clientY > box.bottom
    );
  };

  return createPortal(
    <dialog
      {...props}
      ref={dialogRef}
      className={className}
      aria-modal="true"
      aria-busy={pending || undefined}
      closedby={pending ? 'none' : 'closerequest'}
      onKeyDown={(event) => {
        // Handle Escape before native close-watcher processing, which can emit
        // a non-cancelable request after repeated/nested dismissals.
        if (event.key === 'Escape') {
          event.preventDefault();
          event.stopPropagation();
          requestClose();
        }
      }}
      onCancel={(event) => {
        event.preventDefault();
        event.stopPropagation();
        requestClose();
      }}
      onPointerDown={(event) => {
        event.stopPropagation();
        pointerStartedOutside.current = isOutside(event);
      }}
      onClick={(event) => {
        event.stopPropagation();
        if (closeOnBackdrop && pointerStartedOutside.current && isOutside(event)) requestClose();
        pointerStartedOutside.current = false;
      }}
    >
      {children}
    </dialog>,
    document.body,
  );
}
