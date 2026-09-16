// Chrome around one placed widget.
//
// Origin: ServerKit `frontend/src/components/dashboard/grid/WidgetFrame.jsx`
// (MIT, same owner). Changed here: the header only exists in Arrange mode —
// on the desk a widget is its own content over a wallpaper, not a titled
// panel — and the icon buttons are Vela's `ui/Button` with Phosphor glyphs
// instead of ServerKit's Radix button and lucide.
//
// Positioning is owned by DeskGrid and arrives through `style`.
import { useEffect, useRef, useState } from 'react';
import { Copy, DotsSixVertical, DotsThree, Sliders, Trash } from '@phosphor-icons/react';
import Button from '../../components/ui/Button.jsx';
import { deriveWidgetTitle } from '../registry.js';
import { WidgetBoundary } from '../widgets/primitives.jsx';

export default function WidgetFrame({
  widget,
  type,
  ctx,
  edit = false,
  selected = false,
  onSelect,
  onMenu,
  onViewMenu,
  onDragStart,
  onResizeStart,
  style,
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);

  // Close the overflow menu on any outside pointer press or Escape. Bound only
  // while the menu is open, and always torn down.
  useEffect(() => {
    if (!menuOpen) return undefined;
    const onPointerDown = (event) => {
      if (!menuRef.current?.contains(event.target)) setMenuOpen(false);
    };
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        setMenuOpen(false);
      }
    };
    addEventListener('pointerdown', onPointerDown, true);
    addEventListener('keydown', onKeyDown, true);
    return () => {
      removeEventListener('pointerdown', onPointerDown, true);
      removeEventListener('keydown', onKeyDown, true);
    };
  }, [menuOpen]);

  const title = deriveWidgetTitle(widget, type);
  const Render = type?.render;
  const fire = (action) => {
    setMenuOpen(false);
    onMenu?.(action, widget);
  };

  const className = ['desk-frame', edit && 'desk-frame-edit', selected && 'desk-frame-selected']
    .filter(Boolean)
    .join(' ');

  return (
    <section
      className={className}
      style={style}
      aria-label={title}
      data-widget={widget.i}
      tabIndex={edit ? 0 : undefined}
      onPointerDown={() => {
        if (edit) onSelect?.(widget.i);
      }}
      onContextMenu={(event) => {
        // In view mode a right-click on a widget offers its own actions; in
        // edit mode the frame keeps its overflow menu.
        if (edit || !onViewMenu) return;
        event.preventDefault();
        onViewMenu(widget, event.clientX, event.clientY);
      }}
    >
      {edit && (
        <div
          className="desk-frame-head"
          onPointerDown={(event) => {
            if (!event.target.closest('button')) onDragStart?.(event, widget);
          }}
        >
          <span className="desk-frame-grip" aria-hidden="true">
            <DotsSixVertical size={14} weight="bold" />
          </span>
          <span className="desk-frame-title">{title}</span>
          <div className="desk-frame-actions" ref={menuRef}>
            {type?.options && (
              <Button
                size="icon"
                variant="ghost"
                aria-label={`Options for ${title}`}
                onClick={() => fire('config')}
              >
                <Sliders size={15} aria-hidden="true" />
              </Button>
            )}
            <Button
              size="icon"
              variant="ghost"
              aria-label={`Menu for ${title}`}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((open) => !open)}
            >
              <DotsThree size={17} weight="bold" aria-hidden="true" />
            </Button>
            {menuOpen && (
              <div className="desk-frame-menu" role="menu" aria-label={`${title} options`}>
                <button type="button" role="menuitem" onClick={() => fire('duplicate')}>
                  <Copy size={15} aria-hidden="true" />
                  <span>Duplicate</span>
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className="is-danger"
                  onClick={() => fire('remove')}
                >
                  <Trash size={15} aria-hidden="true" />
                  <span>Remove</span>
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="desk-frame-body">
        <WidgetBoundary key={`${widget.i}:${widget.type}`} widgetType={widget.type}>
          {Render ? (
            <Render widget={widget} cfg={widget.cfg || {}} type={type} ctx={ctx} />
          ) : (
            <p className="desk-empty">This widget is no longer available.</p>
          )}
        </WidgetBoundary>
      </div>

      {edit && (
        // Pointer affordance only; the keyboard path to resizing is
        // Shift + arrow keys on the focused frame.
        <span
          className="desk-frame-resize"
          aria-hidden="true"
          onPointerDown={(event) => onResizeStart?.(event, widget)}
        />
      )}
    </section>
  );
}
