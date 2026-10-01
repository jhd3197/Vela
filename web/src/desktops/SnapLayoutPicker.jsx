// The layout picker: small drawings of the ways a screen can be divided, each
// cell a button that puts the window there.
//
// It opens from three places, all asking for the same thing: resting the
// pointer on a window's maximize button, the window menu, and Alt+Z. From the
// keyboard it takes focus on its first cell, the arrow keys move between cells
// and Escape puts it away; from a hover it waits to be reached and closes a
// moment after the pointer leaves both it and the button, so passing over the
// button on the way somewhere else does nothing.
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { LAYOUTS } from './layouts.js';

const MARGIN = 8;

export default function SnapLayoutPicker({
  x,
  y,
  title,
  focus = false,
  onPick,
  onClose,
  onPointerEnter,
  onPointerLeave,
}) {
  const panel = useRef(null);
  const [place, setPlace] = useState({ x, y, ready: false });

  // Measured before it is shown, and kept on screen: it opens under a button
  // that can be anywhere a window can be.
  useLayoutEffect(() => {
    const box = panel.current?.getBoundingClientRect();
    if (!box) return;
    const left = Math.min(Math.max(MARGIN, x - box.width / 2), innerWidth - box.width - MARGIN);
    const top = y + box.height + MARGIN > innerHeight ? Math.max(MARGIN, y - box.height - 44) : y;
    setPlace({ x: left, y: top, ready: true });
  }, [x, y]);

  useEffect(() => {
    if (focus && place.ready)
      panel.current?.querySelector('button')?.focus({ preventScroll: true });
  }, [focus, place.ready]);

  useEffect(() => {
    const onKey = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        onClose?.({ restoreFocus: true });
        return;
      }
      const cells = Array.from(panel.current?.querySelectorAll('button') || []);
      const index = cells.indexOf(document.activeElement);
      if (index === -1) return;
      const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
      if (step) {
        event.preventDefault();
        cells[(index + step + cells.length) % cells.length]?.focus();
      } else if (event.key === 'Tab') {
        // A popup, not a page: leaving it puts it away.
        event.preventDefault();
        onClose?.({ restoreFocus: true });
      }
    };
    const onPointer = (event) => {
      if (!panel.current?.contains(event.target)) onClose?.();
    };
    addEventListener('keydown', onKey, true);
    addEventListener('pointerdown', onPointer, true);
    return () => {
      removeEventListener('keydown', onKey, true);
      removeEventListener('pointerdown', onPointer, true);
    };
  }, [onClose]);

  return createPortal(
    <div
      ref={panel}
      className={`snap-layouts${place.ready ? ' is-ready' : ''}`}
      role="dialog"
      aria-label={`Snap layouts for ${title}`}
      style={{ left: `${place.x}px`, top: `${place.y}px` }}
      onPointerEnter={onPointerEnter}
      onPointerLeave={onPointerLeave}
    >
      {LAYOUTS.map((layout) => (
        <div key={layout.id} className="snap-layout" role="group" aria-label={layout.name}>
          {layout.cells.map((cell) => (
            <button
              key={cell.id}
              type="button"
              className="snap-layout-cell"
              aria-label={cell.name}
              title={cell.name}
              // A cell's place in its drawing is its own fraction of the screen.
              style={{
                left: `${cell.x * 100}%`,
                top: `${cell.y * 100}%`,
                width: `${cell.w * 100}%`,
                height: `${cell.h * 100}%`,
              }}
              onClick={() => onPick?.(cell)}
            />
          ))}
        </div>
      ))}
    </div>,
    document.body,
  );
}
