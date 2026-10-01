import { useRef } from 'react';
import Dialog from './Dialog.jsx';
import Button from './Button.jsx';
import { comboKeys, sheetGroups } from '../../shell/keys.js';

// The keyboard shortcuts, listed. Opened with `?` or Ctrl+/ from anywhere. The
// rows come from `shell/keys.js`, the same list the listeners match against,
// so a shortcut cannot exist without being listed here or be listed without
// existing.

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
      {sheetGroups().map((group) => (
        <section key={group.name} className="shortcut-group" aria-label={group.name}>
          <h3 className="shortcut-group-title">{group.name}</h3>
          <ul className="shortcut-list">
            {group.shortcuts.map((row) => (
              <li key={row.id}>
                <span className="shortcut-combo">
                  <Combo keys={comboKeys(row.combo)} />
                  {row.through ? (
                    <>
                      <span className="shortcut-to" aria-hidden="true">
                        –
                      </span>
                      <Combo keys={comboKeys(row.through)} />
                    </>
                  ) : null}
                  {row.also ? (
                    <>
                      <span className="shortcut-to">or</span>
                      <Combo keys={comboKeys(row.also)} />
                    </>
                  ) : null}
                </span>
                <span className="shortcut-label">{row.label}</span>
              </li>
            ))}
          </ul>
        </section>
      ))}
      <p className="shortcut-note">
        Keys typed inside an app go to that app. To use a window shortcut, click the window&rsquo;s
        title bar first.
      </p>
      <div className="form-actions">
        <Button ref={closeRef} variant="primary" onClick={onClose}>
          Done
        </Button>
      </div>
    </Dialog>
  );
}
