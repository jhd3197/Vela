import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

// A floating menu anchored to a point: the right-click / long-press menu the
// Launchpad, the rail and the desk share. It is controlled — the caller owns
// where it opens and what it lists — and it owns the parts a menu must always
// get right: roving arrow-key focus over the items, Enter or Space to choose,
// Escape and an outside press to dismiss, and focus returned to whatever opened
// it. It renders in a portal so a menu opened from a tile is never clipped by
// the tile's own overflow, and it nudges itself back on screen when it would
// spill past an edge.
//
// `items` is a flat list; an entry with `separator: true` draws a divider, and
// any other entry is a button: `{ label, icon: Icon, onSelect, disabled,
// danger }`. A disabled entry stays visible but is skipped by the arrow keys.

function focusables(root) {
  if (!root) return [];
  return Array.from(root.querySelectorAll('button:not([disabled])'));
}

export default function ContextMenu({
  open,
  x,
  y,
  onClose,
  items,
  label = 'Actions',
  returnFocusRef,
}) {
  const menuRef = useRef(null);
  const [pos, setPos] = useState({ x, y });

  useLayoutEffect(() => {
    if (!open) return;
    setPos({ x, y });
  }, [open, x, y]);

  // Keep the menu inside the viewport: if it would run off the right or bottom
  // edge, pull it back by its own overflow rather than letting it clip.
  useLayoutEffect(() => {
    if (!open) return;
    const menu = menuRef.current;
    if (!menu) return;
    const box = menu.getBoundingClientRect();
    let nextX = x;
    let nextY = y;
    const margin = 8;
    if (box.width && x + box.width > window.innerWidth - margin) {
      nextX = Math.max(margin, window.innerWidth - box.width - margin);
    }
    if (box.height && y + box.height > window.innerHeight - margin) {
      nextY = Math.max(margin, window.innerHeight - box.height - margin);
    }
    if (nextX !== x || nextY !== y) setPos({ x: nextX, y: nextY });
  }, [open, x, y, items]);

  useEffect(() => {
    if (!open) return undefined;
    const first = focusables(menuRef.current)[0];
    first?.focus({ preventScroll: true });
  }, [open]);

  const close = useCallback(() => {
    onClose?.();
    const back = returnFocusRef?.current;
    if (back?.isConnected && typeof back.focus === 'function') back.focus({ preventScroll: true });
  }, [onClose, returnFocusRef]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => {
      const list = focusables(menuRef.current);
      if (list.length === 0) return;
      const index = list.indexOf(document.activeElement);
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
      } else if (event.key === 'ArrowDown') {
        event.preventDefault();
        list[(index + 1 + list.length) % list.length]?.focus();
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        list[(index - 1 + list.length) % list.length]?.focus();
      } else if (event.key === 'Home') {
        event.preventDefault();
        list[0]?.focus();
      } else if (event.key === 'End') {
        event.preventDefault();
        list[list.length - 1]?.focus();
      } else if (event.key === 'Tab') {
        // A menu is a trap while it is open; leaving it closes it.
        event.preventDefault();
        close();
      }
    };
    const onPointer = (event) => {
      if (menuRef.current?.contains(event.target)) return;
      close();
    };
    window.addEventListener('keydown', onKey, true);
    window.addEventListener('pointerdown', onPointer, true);
    window.addEventListener('resize', close);
    return () => {
      window.removeEventListener('keydown', onKey, true);
      window.removeEventListener('pointerdown', onPointer, true);
      window.removeEventListener('resize', close);
    };
  }, [open, close]);

  if (!open) return null;

  return createPortal(
    <div
      ref={menuRef}
      className="context-menu"
      role="menu"
      aria-label={label}
      style={{ left: pos.x, top: pos.y }}
      onContextMenu={(event) => event.preventDefault()}
    >
      {items.map((item, index) => {
        if (item.separator)
          return <span key={`sep-${index}`} className="context-menu-sep" aria-hidden="true" />;
        const Icon = item.icon;
        return (
          <button
            key={item.label}
            type="button"
            role="menuitem"
            className={`context-menu-item${item.danger ? ' is-danger' : ''}`}
            disabled={item.disabled}
            onClick={() => {
              // Return focus to the opener before running the action, so a
              // dialog the action opens records the opener and lands focus back
              // there on close.
              const back = returnFocusRef?.current;
              onClose?.();
              if (back?.isConnected && typeof back.focus === 'function') {
                back.focus({ preventScroll: true });
              }
              item.onSelect?.();
            }}
          >
            {Icon ? (
              <Icon size={16} aria-hidden="true" />
            ) : (
              <span className="context-menu-gap" aria-hidden="true" />
            )}
            <span className="context-menu-label">{item.label}</span>
          </button>
        );
      })}
    </div>,
    document.body,
  );
}
