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
// danger, shortcut }`. A disabled entry stays visible but is skipped by the
// arrow keys. `shortcut` is the key combination that does the same thing from
// anywhere, shown beside the label so the menu teaches it.
//
// Two habits from desktop menus are kept. Typing a letter moves to the next
// item that starts with it. And a menu opened by pressing the right button can
// be chosen from by releasing over an item, without a second click.

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
  // Whether a press has started inside the menu since it opened. Until one
  // has, a release over an item is the end of the gesture that opened it.
  const pressed = useRef(false);
  const openedAt = useRef(0);

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
    pressed.current = false;
    openedAt.current = performance.now();
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
      } else if (
        event.key.length === 1 &&
        event.key !== ' ' &&
        !event.ctrlKey &&
        !event.metaKey &&
        !event.altKey
      ) {
        // Type-ahead: the next item, after this one, whose label starts with
        // the letter. Pressing it again walks through them in turn.
        const letter = event.key.toLocaleLowerCase();
        const ordered = [...list.slice(index + 1), ...list.slice(0, index + 1)];
        const match = ordered.find((item) =>
          item.textContent.trim().toLocaleLowerCase().startsWith(letter),
        );
        if (match) {
          event.preventDefault();
          match.focus();
        }
      }
    };
    const onPointer = (event) => {
      if (menuRef.current?.contains(event.target)) {
        pressed.current = true;
        return;
      }
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
        const choose = () => {
          // Return focus to the opener before running the action, so a
          // dialog the action opens records the opener and lands focus back
          // there on close.
          const back = returnFocusRef?.current;
          onClose?.();
          if (back?.isConnected && typeof back.focus === 'function') {
            back.focus({ preventScroll: true });
          }
          item.onSelect?.();
        };
        return (
          <button
            key={item.label}
            type="button"
            role="menuitem"
            className={`context-menu-item${item.danger ? ' is-danger' : ''}`}
            disabled={item.disabled}
            aria-keyshortcuts={item.shortcut ? item.shortcut.replace(/\s+/g, '') : undefined}
            onClick={choose}
            onPointerUp={(event) => {
              // Press, drag onto an item, release: the release chooses it. A
              // release that comes right after opening is the same press that
              // opened the menu on a platform that opens it on release, so it
              // is left alone.
              if (pressed.current || event.button !== 2) return;
              if (performance.now() - openedAt.current < 250) return;
              event.preventDefault();
              choose();
            }}
          >
            {Icon ? (
              <Icon size={16} aria-hidden="true" />
            ) : (
              <span className="context-menu-gap" aria-hidden="true" />
            )}
            <span className="context-menu-label">{item.label}</span>
            {item.shortcut ? (
              <kbd className="context-menu-shortcut" aria-hidden="true">
                {item.shortcut}
              </kbd>
            ) : null}
          </button>
        );
      })}
    </div>,
    document.body,
  );
}
