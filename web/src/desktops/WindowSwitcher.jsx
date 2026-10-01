// The window switcher: Alt+` with Alt held, the way Alt+Tab is everywhere else.
//
// It lists every window on this desktop, the one in front first, and starts on
// the one before it — so a single tap goes back to whatever you were in before,
// and holding Alt while pressing ` again walks further. Letting go of Alt is the
// choice. It is drawn only while it is being used, and it never moves or
// remounts a window: choosing one is the same select-and-raise a click is.
//
// Owned by the window host, which knows the windows and how to bring one back;
// this only draws the list and says which entry is chosen.
import { createPortal } from 'react-dom';

export default function WindowSwitcher({ entries, index, onChoose }) {
  if (!entries?.length) return null;
  const chosen = entries[index] || entries[0];
  return createPortal(
    <div className="window-switcher" role="presentation">
      <ul className="window-switcher-list" role="listbox" aria-label="Open windows">
        {entries.map((entry, position) => (
          <li
            key={entry.id}
            role="option"
            aria-selected={position === index}
            className={`window-switcher-item${position === index ? ' is-chosen' : ''}${
              entry.minimized ? ' is-minimized' : ''
            }`}
            // Pressed rather than clicked: Alt is still held, and the release of
            // Alt is what commits from the keyboard. A press is its own choice.
            onPointerDown={(event) => {
              event.preventDefault();
              onChoose(entry.id);
            }}
          >
            <span className="window-switcher-icon" aria-hidden="true">
              {entry.icon || (
                <span className="window-switcher-mark">{entry.label.slice(0, 1)}</span>
              )}
            </span>
            <span className="window-switcher-label">{entry.label}</span>
            {entry.minimized ? <span className="window-switcher-state">Minimized</span> : null}
          </li>
        ))}
      </ul>
      <p className="sr-only" aria-live="polite">
        {`${chosen.label}${chosen.minimized ? ', minimized' : ''}, ${index + 1} of ${entries.length}`}
      </p>
    </div>,
    document.body,
  );
}
