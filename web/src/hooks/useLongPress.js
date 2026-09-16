// A press that has to rest before it counts. Extracted from the desk's own
// long-press so the Launchpad, the rail and the desk share one gesture: long
// enough not to fire while scrolling, short enough to feel deliberate, and
// cancelled by any real movement. Mouse input is left alone — a mouse has its
// own `contextmenu` event — so this only arms for touch and pen.
import { useCallback, useEffect, useRef } from 'react';

const LONG_PRESS_MS = 500;
const LONG_PRESS_SLOP = 8;

export default function useLongPress(
  onLongPress,
  { ms = LONG_PRESS_MS, slop = LONG_PRESS_SLOP } = {},
) {
  const press = useRef(null);
  const handler = useRef(onLongPress);
  handler.current = onLongPress;

  const clear = useCallback(() => {
    if (press.current) clearTimeout(press.current.timer);
    press.current = null;
  }, []);

  useEffect(() => clear, [clear]);

  const onPointerDown = useCallback(
    (event) => {
      if (event.pointerType === 'mouse') return;
      const { clientX, clientY, currentTarget } = event;
      press.current = {
        x: clientX,
        y: clientY,
        timer: setTimeout(() => {
          press.current = null;
          handler.current?.({ x: clientX, y: clientY, target: currentTarget });
        }, ms),
      };
    },
    [ms],
  );

  const onPointerMove = useCallback(
    (event) => {
      const started = press.current;
      if (!started) return;
      if (
        Math.abs(event.clientX - started.x) > slop ||
        Math.abs(event.clientY - started.y) > slop
      ) {
        clear();
      }
    },
    [clear, slop],
  );

  return { onPointerDown, onPointerMove, onPointerUp: clear, onPointerCancel: clear };
}
