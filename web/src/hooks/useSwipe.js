import { useRef } from 'react';

// A vertical swipe on touch or pen. Returns pointer handlers to spread onto an
// element; a swipe past `threshold` in a direction fires the matching callback
// once. Mouse input is ignored — a mouse has scrollbars and shortcuts instead.
export default function useSwipe({ onUp, onDown, threshold = 80, slop = 30 } = {}) {
  const start = useRef(null);

  const reset = () => {
    start.current = null;
  };

  return {
    onPointerDown: (event) => {
      if (event.pointerType === 'mouse') return;
      start.current = { x: event.clientX, y: event.clientY };
    },
    onPointerMove: (event) => {
      const from = start.current;
      if (!from) return;
      const dy = event.clientY - from.y;
      const dx = event.clientX - from.x;
      // A mostly-sideways drag is a scroll or a swipe between things, not this.
      if (Math.abs(dx) > Math.abs(dy) + slop) {
        reset();
        return;
      }
      if (dy < -threshold && onUp) {
        reset();
        onUp();
      } else if (dy > threshold && onDown) {
        reset();
        onDown();
      }
    },
    onPointerUp: reset,
    onPointerCancel: reset,
  };
}
