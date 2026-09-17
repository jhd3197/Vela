// Personalise: the small set of choices that make the desk feel like the
// user's own machine rather than a product's home screen.
//
// Everything here is a real setting the server stores. There are no themes
// that do nothing and no toggles that only change a preview.
import { useRef, useState } from 'react';
import { Check, Trash, UploadSimple } from '@phosphor-icons/react';
import { api } from '../api.js';
import { useDesktops } from '../desktops/DesktopsProvider.jsx';
import { BUNDLED_WALLPAPERS, DEFAULT_WALLPAPER, dailyWallpaper } from './wallpaper.js';
import Drawer from '../components/ui/Drawer.jsx';
import Button from '../components/ui/Button.jsx';

// The eight painted places ship as images; `sage` and `night` are gradients
// drawn in CSS, so they cost nothing to bundle and stay sharp at any size.
// `daily` is not a picture but a standing choice: rotate through the painted
// set, a new one at each local midnight. `custom` is the image the user
// uploaded.
export const WALLPAPERS = [
  ...BUNDLED_WALLPAPERS.map(({ id, name }) => ({ id, name })),
  { id: 'sage', name: 'Sage' },
  { id: 'night', name: 'Night' },
  { id: 'daily', name: 'Daily' },
];

const MAX_BYTES = 8 * 1024 * 1024;
const TYPES = ['image/jpeg', 'image/png', 'image/webp'];

export default function PersonaliseSheet({ desk, onChange, onClose, askOn, onToggleAsk }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const file = useRef(null);
  // How a desk is dressed belongs to the desktop being looked at; the weather
  // is one setting for the whole server. The two halves are shown together
  // because that is how a person thinks about them, and saved apart because
  // that is what they are.
  const { selected, appearance, saveAppearance, uploadWallpaper, removeWallpaper } = useDesktops();

  // A toggle moves when it is pressed, not when the server answers: waiting
  // makes a switch feel broken. A failed save puts it back and says why.
  const patch = async (change) => {
    const previous = desk;
    setNote('');
    onChange({ ...desk, ...change });
    try {
      await saveAppearance(change);
    } catch (error) {
      onChange(previous);
      setNote(error.message || 'Could not save that.');
    }
  };

  // The weather is the one thing on this sheet that reaches the internet, so it
  // is turned on by naming a place rather than by a switch alone: without
  // coordinates there is nothing to ask for, and Vela would rather ask nothing.
  const [place, setPlace] = useState('');
  const [locating, setLocating] = useState(false);
  const weather = desk.weather || {};
  const located = typeof weather.latitude === 'number' && typeof weather.longitude === 'number';

  const setWeather = async (change) => {
    const previous = desk;
    setNote('');
    onChange({ ...desk, weather: { ...weather, ...change } });
    try {
      await api.updateSettings({ desk: { weather: { ...weather, ...change } } });
    } catch (error) {
      onChange(previous);
      setNote(error.message || 'Could not save that.');
    }
  };

  const findPlace = async (event) => {
    event.preventDefault();
    const typed = place.trim();
    if (!typed) return;
    setLocating(true);
    setNote('');
    try {
      const found = await api.locateWeather(typed);
      await setWeather({
        enabled: true,
        latitude: found.latitude,
        longitude: found.longitude,
        label: found.label,
      });
      setPlace('');
    } catch (error) {
      setNote(error.message || 'Could not find that place.');
    } finally {
      setLocating(false);
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
      await uploadWallpaper(chosen);
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
      await removeWallpaper();
      onChange({ ...desk, wallpaper: DEFAULT_WALLPAPER });
    } catch (error) {
      setNote(error.message || 'Could not remove it.');
    } finally {
      setBusy(false);
    }
  };

  const choices = [...WALLPAPERS, { id: 'custom', name: 'Yours' }];

  // The painted set and Daily preview as real thumbnails, which is why they
  // carry an inline image rather than a rule each; the gradients and the
  // uploaded image keep their CSS previews. Daily shows the picture it would
  // draw today, so the choice is not a mystery until midnight.
  const thumb = (id) => {
    if (id === 'custom') {
      // This desktop's own image, at its own address; a stylesheet has no way
      // to know which one is being looked at.
      return appearance.customUrl
        ? { '--personalise-custom': `url('${appearance.customUrl}')` }
        : undefined;
    }
    const painted = id === 'daily' ? dailyWallpaper() : id;
    if (!BUNDLED_WALLPAPERS.some((wall) => wall.id === painted)) return undefined;
    return { backgroundImage: `url('/wallpapers/thumbs/${painted}.jpg')` };
  };

  return (
    <Drawer open onClose={onClose} aria-label="Personalise" panelClassName="personalise">
      <div className="drawer-header">
        <div className="drawer-title-row">
          <h2 className="drawer-name">Personalise</h2>
          {selected ? <span className="drawer-scope">{selected.name}</span> : null}
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
                <span
                  className="personalise-wall-preview"
                  style={thumb(choice.id)}
                  aria-hidden="true"
                />
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
          <p className="panel-note">
            The wallpaper, dimming and labels belong to this desktop; your other desktops keep their
            own. Daily moves through the painted set, a new one each midnight. Your own image can be
            JPEG, PNG or WebP, up to 8 MB. It stays on this computer.
          </p>
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

        <section className="personalise-section">
          <h3 className="section-head">Weather</h3>
          <label className="personalise-row">
            <span>
              Show the weather
              <small>
                {located
                  ? `The temperature for ${weather.label || 'your place'} on the clock widget.`
                  : 'Name a place below to turn this on.'}
              </small>
            </span>
            <input
              type="checkbox"
              checked={Boolean(weather.enabled)}
              disabled={busy || !located}
              onChange={(event) => setWeather({ enabled: event.target.checked })}
            />
          </label>
          <form className="personalise-place" onSubmit={findPlace}>
            <label className="sr-only" htmlFor="personalise-place">
              Town or city
            </label>
            <input
              id="personalise-place"
              type="text"
              className="field"
              placeholder={located ? weather.label : 'Town or city'}
              value={place}
              disabled={busy || locating}
              onChange={(event) => setPlace(event.target.value)}
            />
            <Button type="submit" disabled={busy || locating || !place.trim()}>
              {locating ? 'Looking…' : 'Find'}
            </Button>
          </form>
          <p className="panel-note">
            This is the only thing on your desk that leaves this computer. Vela asks Open-Meteo for
            the temperature at the place you name, at most four times an hour, and sends nothing
            else — no account, no identifier, and nothing about your apps. The place is looked up
            once and only its coordinates are kept.
          </p>
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
