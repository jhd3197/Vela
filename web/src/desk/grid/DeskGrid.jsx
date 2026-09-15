// The board host.
//
// Origin: ServerKit `frontend/src/components/dashboard/grid/DashGrid.jsx`
// (MIT, same owner). Changed here: `cols`, `rowHeight`, `gap` and `types`
// arrive as props instead of module constants, because the desk renders a
// 6-column desktop board and a 2-column phone board from the same component;
// i18next and lucide are gone; the classes are `desk-*`.
//
// It measures its own width, derives the cell size, absolutely positions every
// WidgetFrame and drives pointer move/resize with a live preview of the
// resulting layout.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getWidgetType } from '../registry.js';
import { pushDown } from './layout.js';
import WidgetFrame from './WidgetFrame.jsx';

// Breathing room under the last row so the resize grip is never flush with the
// page edge.
const HOST_PAD = 8;
// Fallback minimum span when a widget type declares none.
const FALLBACK_MIN = [1, 1];

const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

export default function DeskGrid({
  widgets = [],
  types = [],
  cols = 6,
  rowHeight = 150,
  gap = 16,
  edit = false,
  selectedId = null,
  onSelect,
  onChange,
  ctx,
  onWidgetMenu,
  empty = null,
}) {
  const hostRef = useRef(null);
  const [hostWidth, setHostWidth] = useState(0);
  const [drag, setDrag] = useState(null);
  // Detach function for the in-flight pointer gesture, so a mid-drag unmount
  // (or a pointer released outside the viewport) never leaves listeners behind.
  const detachRef = useRef(null);

  useEffect(() => {
    const element = hostRef.current;
    if (!element) return undefined;
    const measure = () => setHostWidth(element.clientWidth);
    measure();
    if (typeof ResizeObserver === 'undefined') {
      addEventListener('resize', measure);
      return () => removeEventListener('resize', measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(
    () => () => {
      detachRef.current?.();
    },
    [],
  );

  const cell = hostWidth > 0 ? (hostWidth - gap * (cols - 1)) / cols : 0;
  const stepX = cell + gap;
  const stepY = rowHeight + gap;

  // While dragging we paint the previewed layout instead of the committed one.
  const list = drag ? drag.preview : widgets;
  const rows = Math.max(1, ...list.map((widget) => widget.y + widget.h));

  const beginGesture = useCallback(
    (event, widget, mode) => {
      if (!edit || cell <= 0) return;
      event.preventDefault();
      event.stopPropagation();
      // A second pointer landing mid-gesture must not orphan the first one's
      // window listeners.
      detachRef.current?.();

      const originX = event.clientX;
      const originY = event.clientY;
      const base = { ...widget };
      const type = getWidgetType(types, widget.type);
      const [minW, minH] = type?.min || FALLBACK_MIN;
      const handle = event.currentTarget;
      const { pointerId } = event;
      let latest = widgets;

      // Capture so the gesture keeps tracking when the pointer leaves the
      // handle; the events still bubble up to our window listeners.
      try {
        handle.setPointerCapture?.(pointerId);
      } catch {
        // Capture is best-effort.
      }

      const handleMove = (moveEvent) => {
        const dx = Math.round((moveEvent.clientX - originX) / stepX);
        const dy = Math.round((moveEvent.clientY - originY) / stepY);
        const next =
          mode === 'move'
            ? {
                ...base,
                x: clamp(base.x + dx, 0, cols - base.w),
                y: Math.max(0, base.y + dy),
              }
            : {
                ...base,
                w: Math.max(Math.min(minW, cols), Math.min(cols - base.x, base.w + dx)),
                h: Math.max(minH, base.h + dy),
              };
        latest = pushDown(widgets, next);
        setDrag({ mode, id: widget.i, preview: latest });
      };

      const detach = () => {
        removeEventListener('pointermove', handleMove);
        removeEventListener('pointerup', finishGesture);
        removeEventListener('pointercancel', finishGesture);
        try {
          handle.releasePointerCapture?.(pointerId);
        } catch {
          // Already released.
        }
        detachRef.current = null;
      };

      function finishGesture() {
        detach();
        setDrag(null);
        if (latest !== widgets) onChange?.(latest);
      }

      addEventListener('pointermove', handleMove);
      addEventListener('pointerup', finishGesture);
      addEventListener('pointercancel', finishGesture);
      detachRef.current = detach;

      setDrag({ mode, id: widget.i, preview: widgets });
      onSelect?.(widget.i);
    },
    [edit, cell, cols, stepX, stepY, types, widgets, onChange, onSelect],
  );

  // Only the cell maths is inline; `position` and the settle transition belong
  // to `.desk-grid > .desk-frame` in the stylesheet.
  const frameStyle = useCallback(
    (widget) => ({
      left: widget.x * stepX,
      top: widget.y * stepY,
      width: widget.w * cell + (widget.w - 1) * gap,
      height: widget.h * rowHeight + (widget.h - 1) * gap,
      zIndex: drag?.id === widget.i ? 5 : 1,
    }),
    [drag, cell, gap, rowHeight, stepX, stepY],
  );

  const hostClassName = useMemo(
    () =>
      ['desk-grid', edit && 'desk-grid-edit', drag && 'desk-grid-dragging']
        .filter(Boolean)
        .join(' '),
    [edit, drag],
  );

  return (
    <div
      ref={hostRef}
      className={hostClassName}
      style={{ height: list.length ? rows * rowHeight + (rows - 1) * gap + HOST_PAD : undefined }}
      onPointerDown={(event) => {
        if (edit && event.target === event.currentTarget) onSelect?.(null);
      }}
    >
      {edit && (
        <div
          className="desk-grid-ghost"
          aria-hidden="true"
          style={{ backgroundSize: `${stepX}px ${stepY}px` }}
        />
      )}

      {cell > 0 &&
        list.map((widget) => (
          <WidgetFrame
            key={widget.i}
            widget={widget}
            type={getWidgetType(types, widget.type)}
            ctx={ctx}
            edit={edit}
            selected={selectedId === widget.i}
            onSelect={onSelect}
            onMenu={onWidgetMenu}
            onDragStart={(event, target) => beginGesture(event, target, 'move')}
            onResizeStart={(event, target) => beginGesture(event, target, 'resize')}
            style={frameStyle(widget)}
          />
        ))}

      {list.length === 0 && empty}
    </div>
  );
}
