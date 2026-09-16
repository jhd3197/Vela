import { useRef } from 'react';
import Dialog from './Dialog.jsx';
import Button from './Button.jsx';

// The keyboard shortcuts, listed. Opened with `?` or Ctrl+/ from anywhere.
const SHORTCUTS = [
  { keys: ['Ctrl', 'K'], label: 'Search apps, notes and settings' },
  { keys: ['Ctrl', 'Space'], label: 'Open or close the Launchpad' },
  { keys: ['Ctrl', '1'], to: ['Ctrl', '9'], label: 'Open a pinned app by its place on the rail' },
  { keys: ['Esc'], label: 'Close the Launchpad, a menu or a dialog' },
  { keys: ['?'], label: 'Show this list' },
];

function Combo({ keys }) {
  return (
    <span className="shortcut-keys">
      {keys.map((key, index) => (
        <kbd key={index}>{key}</kbd>
      ))}
    </span>
  );
}

export default function ShortcutSheet({ open, onClose }) {
  const closeRef = useRef(null);
  return (
    <Dialog
      open={open}
      onClose={onClose}
      closeOnBackdrop
      initialFocusRef={closeRef}
      className="modal-dialog shortcut-sheet"
      aria-labelledby="shortcut-sheet-title"
    >
      <h2 id="shortcut-sheet-title">Keyboard shortcuts</h2>
      <ul className="shortcut-list">
        {SHORTCUTS.map((row) => (
          <li key={row.label}>
            <span className="shortcut-combo">
              <Combo keys={row.keys} />
              {row.to ? (
                <>
                  <span className="shortcut-to" aria-hidden="true">
                    –
                  </span>
                  <Combo keys={row.to} />
                </>
              ) : null}
            </span>
            <span className="shortcut-label">{row.label}</span>
          </li>
        ))}
      </ul>
      <div className="form-actions">
        <Button ref={closeRef} variant="primary" onClick={onClose}>
          Done
        </Button>
      </div>
    </Dialog>
  );
}
