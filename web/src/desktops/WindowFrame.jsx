// A window on a desktop: a title bar, its controls and whatever is inside it.
//
// The controls are not interchangeable and the code keeps them apart on
// purpose. **Minimize** hides the window and nothing else — the app keeps
// running, its session stays open and whatever was typed into it is still there
// when it comes back. **Maximize** changes the arrangement. **Close** ends the
// view, which is the only one of the three that asks about unsaved work. A
// prototype that wired Close to the minimize handler is where that confusion
// comes from, and it is not a confusion to ship.
//
// A window that cannot be maximized does not show the control. Offering a
// button that will refuse is worse than not offering it, and the refusal is
// enforced twice anyway: here, where it is never drawn, and in the state
// function behind it, which will not maximize a window whose app said no.
//
// **Placed and resizable are two different things.** A window in a pane, a
// maximized window and a window travelling to the rail are all *placed* — the
// arrangement decides where they are, so dragging one would fight whatever put
// it there. A calculator that only works at one size is *not resizable*, which
// says nothing about whether you may move it. One flag used to mean both, and
// the result was that declaring a fixed size would have nailed the window to
// the desk.
//
// Where the window is drawn comes from `window-state.js`; this only renders it.
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Copy, DotsThree, Minus, Square, X } from '@phosphor-icons/react';
import ContextMenu from '../components/ui/ContextMenu.jsx';
import { moveBounds, resizeBounds } from './window-state.js';

/** The edges a pointer can grab, and the cursor each one shows. */
const EDGES = [
  ['n', 'ns-resize'],
  ['s', 'ns-resize'],
  ['e', 'ew-resize'],
  ['w', 'ew-resize'],
  ['ne', 'nesw-resize'],
  ['nw', 'nwse-resize'],
  ['se', 'nwse-resize'],
  ['sw', 'nesw-resize'],
];

export default function WindowFrame({
  viewId,
  title,
  icon,
  bounds,
  area,
  selected,
  maximized,
  // How far up the stack this window is. Drawn as a layer rather than as a
  // place in the document, because moving the element would reload the app.
  depth = 0,
  // Placed by the arrangement rather than by the pointer.
  placed = false,
  // What the app's manifest said about its own window.
  resizable = true,
  maximizable = true,
  travel,
  status,
  busy = false,
  actions,
  onSelect,
  onMove,
  onDragPoint,
  onMinimize,
  onMaximize,
  onClose,
  children,
}) {
  const titleId = useId();
  const gesture = useRef(null);
  const frame = useRef(null);
  const menuButton = useRef(null);
  const [menu, setMenu] = useState(null);
  // While a gesture is active the window's content takes no pointer input.
  // Pointer capture alone does not survive crossing the app's iframe — the
  // frame's own renderer claims the events and the drag simply stops, which
  // reads as "the window will not move". With the body out of the hit test,
  // the capture holds for the whole gesture.
  const [gesturing, setGesturing] = useState(false);

  const begin = useCallback(
    (event, edge) => {
      // A window in a pane or maximized is placed by the arrangement, not by
      // the pointer; dragging it would fight whatever put it there. A window
      // its app declared fixed-size can still be picked up and moved — the
      // grips are what it does not get.
      if (placed || event.button !== 0) return;
      if (edge && !resizable) return;
      // The controls live in the title bar. Capturing the pointer here would
      // send the pointerup to the bar instead of the button, and the button
      // would never see a click at all.
      if (event.target.closest?.('button')) return;
      event.preventDefault();
      event.currentTarget.setPointerCapture?.(event.pointerId);
      gesture.current = {
        edge,
        startX: event.clientX,
        startY: event.clientY,
        from: bounds,
        moved: false,
      };
      setGesturing(true);
      onSelect?.();
    },
    [placed, resizable, bounds, onSelect],
  );

  const move = useCallback(
    (event) => {
      const active = gesture.current;
      if (!active) return;
      const delta = { x: event.clientX - active.startX, y: event.clientY - active.startY };
      if (!active.moved && Math.abs(delta.x) < 3 && Math.abs(delta.y) < 3) return;
      active.moved = true;
      const next = active.edge
        ? resizeBounds(active.from, delta, active.edge, area)
        : moveBounds(active.from, delta, area);
      if (next) onMove?.(next);
      // Where the pointer is, not where the window is: a snap target is about
      // the edge somebody is reaching for, and a wide window's own left edge is
      // at zero long before they have reached anything.
      if (!active.edge) {
        const box = frame.current?.parentElement?.getBoundingClientRect();
        if (box) onDragPoint?.({ x: event.clientX - box.left, y: event.clientY - box.top });
      }
    },
    [area, onDragPoint, onMove],
  );

  const end = useCallback(
    (event) => {
      const active = gesture.current;
      gesture.current = null;
      setGesturing(false);
      if (!active) return;
      event.currentTarget.releasePointerCapture?.(event.pointerId);
      onDragPoint?.(null, { drop: active.moved && !active.edge });
      if (active.moved) onMove?.(null, { commit: true });
    },
    [onDragPoint, onMove],
  );

  // Escape during a drag puts the window back where it started, which is the
  // only way out of a gesture that has gone somewhere unintended.
  useEffect(() => {
    const onKey = (event) => {
      if (event.key !== 'Escape' || !gesture.current) return;
      const { from } = gesture.current;
      gesture.current = null;
      setGesturing(false);
      onDragPoint?.(null, { cancelled: true });
      onMove?.(from, { commit: true });
    };
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  }, [onDragPoint, onMove]);

  if (!bounds) return null;

  return (
    <section
      ref={frame}
      // `travel` is the window on its way to or from the rail. It is the real
      // window, not a picture of one — the app inside it is still running and
      // must not be remounted — so it is moved by transform and takes no
      // pointer input while it is in flight.
      className={`window-frame${selected ? ' is-selected' : ''}${placed ? ' is-placed' : ''}${
        travel ? ' is-travelling' : ''
      }`}
      style={{
        left: `${bounds.x}px`,
        top: `${bounds.y}px`,
        width: `${bounds.width}px`,
        height: `${bounds.height}px`,
        '--window-depth': depth,
        ...travel,
      }}
      aria-labelledby={titleId}
      data-view-id={viewId}
      onPointerDownCapture={() => onSelect?.()}
    >
      <header
        className="window-bar"
        onPointerDown={(event) => begin(event, null)}
        onPointerMove={move}
        onPointerUp={end}
        onPointerCancel={end}
        onDoubleClick={() => maximizable && onMaximize?.()}
        // The bar's right-click is the window menu, the way a title bar's has
        // always been. Only the bar: right-click inside the window belongs to
        // the app running in it.
        onContextMenu={(event) => {
          // Stopped, not just defaulted: the desk has its own menu for bare
          // wallpaper, and a right-click on a window is not a right-click on
          // the wallpaper underneath it. Claimed even when there is nothing to
          // offer — a fixed-size window has no placement to change — because
          // the alternative is the browser's own menu appearing on a title bar
          // that is Vela's chrome and not a page.
          event.preventDefault();
          event.stopPropagation();
          if (actions?.length) setMenu({ x: event.clientX, y: event.clientY });
        }}
      >
        {icon ? (
          <span className="window-icon" aria-hidden="true">
            {icon}
          </span>
        ) : null}
        <h2 className="window-title" id={titleId}>
          {title}
        </h2>
        {status || busy ? <span className="window-status">{status || 'Opening…'}</span> : null}
        <div className="window-controls">
          {/* Minimize, maximize and close at rest — two of them for a window
              that cannot be maximized. The menu is the extra control only for
              somebody who reaches for it: by keyboard, where it appears on
              focus, or by right-clicking the bar. */}
          {actions?.length ? (
            <button
              type="button"
              className="window-control window-menu sr-only"
              ref={menuButton}
              aria-label={`Window actions for ${title}`}
              aria-haspopup="menu"
              onClick={(event) => {
                const box = event.currentTarget.getBoundingClientRect();
                setMenu({ x: box.left, y: box.bottom + 4 });
              }}
            >
              <DotsThree size={16} weight="bold" aria-hidden="true" />
            </button>
          ) : null}
          <button
            type="button"
            className="window-control"
            aria-label={`Minimize ${title}`}
            onClick={onMinimize}
          >
            <Minus size={14} weight="bold" aria-hidden="true" />
          </button>
          {maximizable ? (
            <button
              type="button"
              className="window-control"
              aria-label={maximized ? `Restore ${title}` : `Maximize ${title}`}
              aria-pressed={maximized}
              onClick={onMaximize}
            >
              {/* One square to fill the desk with; two, offset, to put it back
                  the size it was. The arrows this replaces read as "zoom",
                  which is not what either of them does. */}
              {maximized ? (
                <Copy size={14} weight="bold" aria-hidden="true" />
              ) : (
                <Square size={14} weight="bold" aria-hidden="true" />
              )}
            </button>
          ) : null}
          <button
            type="button"
            className="window-control window-close"
            aria-label={`Close ${title}`}
            onClick={onClose}
          >
            <X size={14} weight="bold" aria-hidden="true" />
          </button>
        </div>
        {/* The wait for a cold launch belongs to the chrome, not the content:
            a hairline sweeping under the bar says the click was received while
            the body shows nothing but the app's own icon. */}
        {busy ? <span className="window-busy-bar" aria-hidden="true" /> : null}
      </header>
      <div className="window-body" style={gesturing ? { pointerEvents: 'none' } : undefined}>
        {children}
      </div>
      {/* Everything a drag can do, available without one. A person using a
          keyboard, a screen reader or a touch device is not a person who should
          be told to drag a title bar to the edge of the screen — the keyboard
          trigger above and the bar's right-click both open this. */}
      {menu && actions?.length ? (
        <ContextMenu
          open
          x={menu.x}
          y={menu.y}
          label={`${title} window`}
          items={actions}
          returnFocusRef={menuButton}
          onClose={() => setMenu(null)}
        />
      ) : null}
      {!placed &&
        resizable &&
        EDGES.map(([edge, cursor]) => (
          <span
            key={edge}
            className={`window-grip window-grip-${edge}`}
            style={{ cursor }}
            aria-hidden="true"
            onPointerDown={(event) => begin(event, edge)}
            onPointerMove={move}
            onPointerUp={end}
            onPointerCancel={end}
          />
        ))}
    </section>
  );
}
