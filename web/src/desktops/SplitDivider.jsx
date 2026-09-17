// The handle between two panes.
//
// Eight pixels wide and considerably more than eight pixels to grab, because
// the visual weight of a divider and the size of a pointer target are two
// different requirements and only one of them is about how it looks.
//
// It is a real separator to a screen reader, with a value and a range, and it
// moves with the arrow keys — Home puts it back to equal halves. A divider that
// could only be dragged would be a divider half the people using Vela could not
// move at all.
//
// Movement is local until the pointer is released. One revisioned save on
// release, rather than a write per frame: the person is still deciding while
// they drag, and a hundred saved opinions are ninety-nine that were never held.
import { useCallback, useEffect, useRef, useState } from 'react';
import { GUTTER, MAX_RATIO, MIN_RATIO, ratioAt, stepRatio } from './snap.js';

export default function SplitDivider({ area, ratio, onPreview, onCommit }) {
  const handle = useRef(null);
  const start = useRef(null);
  const [live, setLive] = useState(null);
  const shown = live ?? ratio;

  const begin = useCallback(
    (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      handle.current?.setPointerCapture?.(event.pointerId);
      start.current = { ratio };
      setLive(ratio);
    },
    [ratio],
  );

  const move = useCallback(
    (event) => {
      if (!start.current) return;
      const box = handle.current?.parentElement?.getBoundingClientRect();
      if (!box) return;
      const next = ratioAt(event.clientX - box.left, area);
      setLive(next);
      onPreview?.(next);
    },
    [area, onPreview],
  );

  const end = useCallback(
    (event) => {
      const gesture = start.current;
      start.current = null;
      if (!gesture) return;
      handle.current?.releasePointerCapture?.(event.pointerId);
      setLive(null);
      onPreview?.(null);
      if (live !== null && live !== gesture.ratio) onCommit?.(live);
    },
    [live, onCommit, onPreview],
  );

  // Escape during a drag puts it back where it started. The same way out as
  // every other gesture on a desktop.
  useEffect(() => {
    const onKey = (event) => {
      if (event.key !== 'Escape' || !start.current) return;
      const { ratio: from } = start.current;
      start.current = null;
      setLive(null);
      onPreview?.(null);
      onCommit?.(from);
    };
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  }, [onCommit, onPreview]);

  const onKeyDown = useCallback(
    (event) => {
      let next = null;
      if (event.key === 'ArrowLeft') next = stepRatio(ratio, -1);
      else if (event.key === 'ArrowRight') next = stepRatio(ratio, 1);
      else if (event.key === 'Home') next = 0.5;
      else if (event.key === 'PageDown') next = stepRatio(ratio, -5);
      else if (event.key === 'PageUp') next = stepRatio(ratio, 5);
      if (next === null) return;
      event.preventDefault();
      onCommit?.(next);
    },
    [onCommit, ratio],
  );

  const usable = Math.max(0, area.width - GUTTER);
  return (
    <div
      ref={handle}
      className={`split-divider${live !== null ? ' is-dragging' : ''}`}
      style={{ left: `${Math.round(usable * shown)}px`, width: `${GUTTER}px` }}
      role="separator"
      tabIndex={0}
      aria-orientation="vertical"
      aria-label="Divider between the two panes"
      aria-valuemin={Math.round(MIN_RATIO * 100)}
      aria-valuemax={Math.round(MAX_RATIO * 100)}
      aria-valuenow={Math.round(shown * 100)}
      aria-valuetext={`${Math.round(shown * 100)}% to the left pane`}
      onPointerDown={begin}
      onPointerMove={move}
      onPointerUp={end}
      onPointerCancel={end}
      onKeyDown={onKeyDown}
      onDoubleClick={() => onCommit?.(0.5)}
    />
  );
}
