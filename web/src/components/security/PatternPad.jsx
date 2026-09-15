import { useCallback, useEffect, useRef, useState } from 'react';
import { DOTS, MIN_DOTS, appendDot } from '../../pattern.js';

const ROWS = [0, 1, 2];
const CENTER = (index) => ({ x: (index % 3) * 50, y: Math.floor(index / 3) * 50 });

// Nine dots in a responsive square. Dragging is the familiar way in; every dot
// is also a real button, so a keyboard or a switch can enter the same pattern
// one dot at a time. The trail is cleared after every attempt.
export default function PatternPad({
  value,
  onChange,
  onComplete,
  disabled = false,
  showTrail = true,
  label = 'Unlock pattern',
}) {
  const [drawing, setDrawing] = useState(false);
  const [sequential, setSequential] = useState(false);
  const surface = useRef(null);
  const completeRef = useRef(onComplete);
  completeRef.current = onComplete;
  const valueRef = useRef(value);
  valueRef.current = value;

  // Hit testing works from the surface's own box, so fast movement, a finger
  // that leaves the grid and a rotated screen all resolve the same way.
  const dotAt = useCallback((clientX, clientY) => {
    const box = surface.current?.getBoundingClientRect();
    if (!box || !box.width) return null;
    const cell = box.width / 3;
    const column = Math.floor((clientX - box.left) / cell);
    const row = Math.floor((clientY - box.top) / cell);
    if (column < 0 || column > 2 || row < 0 || row > 2) return null;
    const index = row * 3 + column;
    const middle = { x: box.left + (column + 0.5) * cell, y: box.top + (row + 0.5) * cell };
    const reach = cell * 0.42;
    return Math.hypot(clientX - middle.x, clientY - middle.y) <= reach ? index : null;
  }, []);

  const finish = useCallback(() => {
    setDrawing(false);
    const drawn = valueRef.current;
    if (drawn.length) completeRef.current?.(drawn);
  }, []);

  useEffect(() => {
    if (!drawing) return undefined;
    // A pointer released or cancelled outside the grid must still end the
    // gesture rather than leaving a stuck trail.
    const end = () => finish();
    addEventListener('pointerup', end);
    addEventListener('pointercancel', end);
    return () => {
      removeEventListener('pointerup', end);
      removeEventListener('pointercancel', end);
    };
  }, [drawing, finish]);

  const visit = (clientX, clientY) => {
    const dot = dotAt(clientX, clientY);
    if (dot === null) return;
    const next = appendDot(valueRef.current, dot);
    if (next !== valueRef.current) onChange(next);
  };

  const trail = showTrail ? value : [];

  return (
    <div className="patternpad">
      <div
        ref={surface}
        className={`pattern-grid${drawing ? ' is-drawing' : ''}${sequential ? ' is-sequential' : ''}`}
        role="group"
        aria-label={label}
        onPointerDown={(event) => {
          if (disabled || sequential || event.button !== 0) return;
          event.currentTarget.setPointerCapture?.(event.pointerId);
          onChange([]);
          valueRef.current = [];
          setDrawing(true);
          visit(event.clientX, event.clientY);
        }}
        onPointerMove={(event) => {
          if (drawing && !disabled) visit(event.clientX, event.clientY);
        }}
        onPointerUp={() => {
          if (drawing) finish();
        }}
      >
        {showTrail && (
          <svg className="pattern-trail" viewBox="-25 -25 150 150" aria-hidden="true">
            {trail.length > 1 && (
              <polyline
                points={trail.map((dot) => `${CENTER(dot).x},${CENTER(dot).y}`).join(' ')}
              />
            )}
          </svg>
        )}
        {ROWS.flatMap((row) =>
          ROWS.map((column) => {
            const index = row * 3 + column;
            const order = value.indexOf(index);
            return (
              <button
                key={index}
                type="button"
                className={`pattern-dot${order >= 0 ? ' is-on' : ''}`}
                aria-pressed={order >= 0}
                aria-label={`Row ${row + 1}, column ${column + 1}${order >= 0 ? `, chosen ${order + 1}` : ''}`}
                disabled={disabled}
                onClick={(event) => {
                  // A pointer drag already recorded this dot. Only a keyboard
                  // or switch activation (detail 0) or sequential entry adds one.
                  if (!sequential && event.detail !== 0) return;
                  onChange(appendDot(value, index));
                }}
              >
                <span aria-hidden="true" />
              </button>
            );
          }),
        )}
      </div>

      <p className="pattern-count" role="status">
        {value.length === 0
          ? `Connect at least ${MIN_DOTS} of the ${DOTS} dots.`
          : `${value.length} dot${value.length === 1 ? '' : 's'} connected.`}
      </p>

      <div className="pattern-actions">
        <button
          type="button"
          className="link-btn"
          onClick={() => {
            setSequential((on) => !on);
            onChange([]);
          }}
        >
          {sequential ? 'Draw the pattern instead' : 'Choose dots one at a time'}
        </button>
        {sequential && (
          <button
            type="button"
            className="btn btn-small"
            disabled={disabled || value.length < MIN_DOTS}
            onClick={() => completeRef.current?.(value)}
          >
            Use this pattern
          </button>
        )}
        <button
          type="button"
          className="link-btn"
          disabled={disabled || value.length === 0}
          onClick={() => onChange([])}
        >
          Clear
        </button>
      </div>
    </div>
  );
}
