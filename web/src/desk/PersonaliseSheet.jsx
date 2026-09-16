// Personalise: the small set of choices that make the desk feel like the
// user's own machine rather than a product's home screen.
//
// Everything here is a real setting the server stores. There are no themes
// that do nothing and no toggles that only change a preview.
import { useRef, useState } from 'react';
import { Check, Trash, UploadSimple } from '@phosphor-icons/react';
import { api } from '../api.js';
import Drawer from '../components/ui/Drawer.jsx';
import Button from '../components/ui/Button.jsx';

// `lake` is a photograph that ships with the dashboard; the other two are
// gradients drawn in CSS, so they cost nothing to bundle and stay sharp at any
// size. `custom` is the image the user uploaded.
export const WALLPAPERS = [
  { id: 'lake', name: 'Lake' },
  { id: 'sage', name: 'Sage' },
  { id: 'night', name: 'Night' },
];

const MAX_BYTES = 8 * 1024 * 1024;
const TYPES = ['image/jpeg', 'image/png', 'image/webp'];

export default function PersonaliseSheet({ desk, onChange, onClose, askOn, onToggleAsk }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const file = useRef(null);

  // A toggle moves when it is pressed, not when the server answers: waiting
  // makes a switch feel broken. A failed save puts it back and says why.
  const patch = async (change) => {
    const previous = desk;
    setNote('');
    onChange({ ...desk, ...change });
    try {
      await api.updateSettings({ desk: change });
    } catch (error) {
      onChange(previous);
      setNote(error.message || 'Could not save that.');
    }
  };

  const upload = async (event) => {
    const chosen = event.target.files?.[0];
    event.target.value = '';
    if (!chosen) return;
    if (!TYPES.includes(chosen.type)) {
      setNote('Choose a JPEG, PNG or WebP image.');
      return;
    }
    if (chosen.size > MAX_BYTES) {
      setNote('That image is larger than 8 MB.');
      return;
    }
    setBusy(true);
    setNote('');
    try {
      await api.putWallpaper(chosen);
      onChange({ ...desk, wallpaper: 'custom' });
    } catch (error) {
      setNote(error.message || 'Could not use that image.');
    } finally {
      setBusy(false);
    }
  };

  const removeCustom = async () => {
    setBusy(true);
    setNote('');
    try {
      await api.deleteWallpaper();
      onChange({ ...desk, wallpaper: 'lake' });
    } catch (error) {
      setNote(error.message || 'Could not remove it.');
    } finally {
      setBusy(false);
    }
  };

  const choices = [...WALLPAPERS, { id: 'custom', name: 'Yours' }];

  return (
    <Drawer open onClose={onClose} aria-label="Personalise" panelClassName="personalise">
      <div className="drawer-header">
        <div className="drawer-title-row">
          <h2 className="drawer-name">Personalise</h2>
        </div>
        <button className="drawer-close" onClick={onClose} aria-label="Close personalise">
          ×
        </button>
      </div>
      <div className="drawer-body">
        <section className="personalise-section">
          <h3 className="section-head">Wallpaper</h3>
          <div className="personalise-walls" role="group" aria-label="Wallpaper">
            {choices.map((choice) => (
              <button
                key={choice.id}
                type="button"
                className={`personalise-wall${desk.wallpaper === choice.id ? ' is-selected' : ''}`}
                data-wallpaper={choice.id}
                disabled={busy || (choice.id === 'custom' && desk.wallpaper !== 'custom')}
                aria-pressed={desk.wallpaper === choice.id}
                onClick={() => patch({ wallpaper: choice.id })}
              >
                <span className="personalise-wall-preview" aria-hidden="true" />
                <span className="personalise-wall-name">
                  {choice.name}
                  {desk.wallpaper === choice.id && <Check size={14} weight="bold" />}
                </span>
              </button>
            ))}
          </div>
          <div className="form-actions">
            <Button disabled={busy} onClick={() => file.current?.click()}>
              <UploadSimple size={15} aria-hidden="true" />
              Use your own
            </Button>
            {desk.wallpaper === 'custom' && (
              <Button variant="ghost" disabled={busy} onClick={removeCustom}>
                <Trash size={15} aria-hidden="true" />
                Remove
              </Button>
            )}
          </div>
          <input
            ref={file}
            type="file"
            className="sr-only"
            accept={TYPES.join(',')}
            aria-label="Choose a wallpaper image"
            onChange={upload}
          />
          <p className="panel-note">JPEG, PNG or WebP, up to 8 MB. It stays on this computer.</p>
        </section>

        <section className="personalise-section">
          <h3 className="section-head">Desk</h3>
          <label className="personalise-row">
            <span>
              Dim the wallpaper
              <small>Keeps widget text readable over a bright picture.</small>
            </span>
            <input
              type="checkbox"
              checked={desk.dim !== false}
              disabled={busy}
              onChange={(event) => patch({ dim: event.target.checked })}
            />
          </label>
          <label className="personalise-row">
            <span>
              Show app names
              <small>Turn off for icons only.</small>
            </span>
            <input
              type="checkbox"
              checked={desk.labels !== false}
              disabled={busy}
              onChange={(event) => patch({ labels: event.target.checked })}
            />
          </label>
          <label className="personalise-row">
            <span>
              Ask on this board
              <small>Adds or removes the Ask widget.</small>
            </span>
            <input
              type="checkbox"
              checked={askOn}
              disabled={busy}
              onChange={(event) => onToggleAsk(event.target.checked)}
            />
          </label>
        </section>

        {note && (
          <p className="inline-error" role="alert">
            {note}
          </p>
        )}
      </div>
    </Drawer>
  );
}
