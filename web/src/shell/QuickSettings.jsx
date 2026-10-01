// Quick settings: the handful of things worth changing without opening
// Settings, one click from the top bar.
//
// Light or dark, this desktop's wallpaper and its two switches, putting the
// windows away, every desktop at a glance, the shortcut list, and — when an
// app lock is set up — locking Vela. Everything here is
// also in Settings or on a key; this is the short way, not the only way.
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  Desktop,
  Image as ImageIcon,
  Keyboard,
  LockSimple,
  Moon,
  SlidersHorizontal,
  SquaresFour,
  Sun,
} from '@phosphor-icons/react';
import { api } from '../api.js';
import { useSecurity } from '../components/SecurityProvider.jsx';
import { useSettingsPopup } from '../components/SettingsProvider.jsx';
import { useDesktops } from '../desktops/DesktopsProvider.jsx';
import { openDesktopOverview } from '../desktops/DesktopOverview.jsx';
import { BUNDLED_WALLPAPERS, resolveWallpaper } from '../desk/wallpaper.js';
import { getTheme, setTheme } from '../theme.js';
import { OPEN_SHORTCUTS, comboLabel } from './keys.js';

/** The next painted wallpaper after the one on screen, wrapping. */
export function nextWallpaper(current) {
  const painted = resolveWallpaper({ wallpaper: current }).id;
  const index = BUNDLED_WALLPAPERS.findIndex((wall) => wall.id === painted);
  return BUNDLED_WALLPAPERS[(index + 1) % BUNDLED_WALLPAPERS.length].id;
}

export default function QuickSettings() {
  const [open, setOpen] = useState(false);
  const button = useRef(null);
  const panel = useRef(null);
  const [place, setPlace] = useState(null);
  const [theme, setThemeState] = useState(getTheme);
  const [note, setNote] = useState('');
  const { appearance, saveAppearance, selected, views } = useDesktops();
  const { openSettings } = useSettingsPopup();
  const { status: security, apply: applySecurity } = useSecurity();
  // Locking is the app lock's own, so it is offered only once one is set up.
  const lockable = Boolean(security?.enrolled && !security?.locked);

  const close = useCallback((restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) button.current?.focus({ preventScroll: true });
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    const box = button.current?.getBoundingClientRect();
    if (box) setPlace({ right: Math.max(8, innerWidth - box.right), top: box.bottom + 8 });
    setThemeState(getTheme());
    setNote('');
    const onKey = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        close(true);
      }
    };
    const onPointer = (event) => {
      if (panel.current?.contains(event.target) || button.current?.contains(event.target)) return;
      close();
    };
    addEventListener('keydown', onKey);
    addEventListener('pointerdown', onPointer, true);
    return () => {
      removeEventListener('keydown', onKey);
      removeEventListener('pointerdown', onPointer, true);
    };
  }, [open, close]);

  useEffect(() => {
    if (open && place) panel.current?.querySelector('button')?.focus({ preventScroll: true });
  }, [open, place]);

  const pickTheme = (next) => {
    const previous = theme;
    setTheme(next);
    setThemeState(next);
    api.updateSettings({ theme: next }).catch((error) => {
      setTheme(previous);
      setThemeState(previous);
      setNote(error.message || 'Could not save the theme.');
    });
  };

  const patchDesk = async (change) => {
    setNote('');
    try {
      await saveAppearance(change);
    } catch (error) {
      setNote(error.message || 'Could not save that.');
    }
  };

  // Each action puts the panel away first, so whatever it opens has the screen.
  const then = (action) => () => {
    close();
    action();
  };

  return (
    <>
      <button
        ref={button}
        type="button"
        className="icon-btn"
        aria-label="Quick settings"
        aria-expanded={open}
        aria-haspopup="dialog"
        title="Quick settings"
        onClick={() => setOpen((value) => !value)}
      >
        <SlidersHorizontal size={17} aria-hidden="true" />
      </button>
      {open && place
        ? createPortal(
            <div
              ref={panel}
              className="quick-settings"
              role="dialog"
              aria-label="Quick settings"
              // Under the button that opened it, measured: the bar's contents move.
              style={{ right: `${place.right}px`, top: `${place.top}px` }}
            >
              <div className="quick-settings-row" role="group" aria-label="Light or dark">
                {[
                  ['light', 'Light', Sun],
                  ['dark', 'Dark', Moon],
                ].map(([value, label, Icon]) => (
                  <button
                    key={value}
                    type="button"
                    className={`quick-tile${theme === value ? ' is-on' : ''}`}
                    aria-pressed={theme === value}
                    onClick={() => pickTheme(value)}
                  >
                    <Icon size={18} aria-hidden="true" />
                    {label}
                  </button>
                ))}
              </div>

              {selected ? (
                <section className="quick-settings-section" aria-label={selected.name}>
                  <h3 className="quick-settings-title">{selected.name}</h3>
                  <button
                    type="button"
                    className="quick-action"
                    onClick={() => patchDesk({ wallpaper: nextWallpaper(appearance.wallpaper) })}
                  >
                    <ImageIcon size={16} aria-hidden="true" />
                    <span className="quick-action-label">Next wallpaper</span>
                  </button>
                  <label className="quick-switch">
                    <input
                      type="checkbox"
                      checked={appearance.dim !== false}
                      onChange={(event) => patchDesk({ dim: event.target.checked })}
                    />
                    Dim the wallpaper
                  </label>
                  <label className="quick-switch">
                    <input
                      type="checkbox"
                      checked={appearance.labels !== false}
                      onChange={(event) => patchDesk({ labels: event.target.checked })}
                    />
                    Label widgets
                  </label>
                </section>
              ) : null}

              <section className="quick-settings-section" aria-label="Windows">
                <button
                  type="button"
                  className="quick-action"
                  disabled={!views.ordered?.length}
                  onClick={then(() => views.showDesktop?.())}
                >
                  <Desktop size={16} aria-hidden="true" />
                  <span className="quick-action-label">Show desktop</span>
                  <kbd>{comboLabel('show-desktop')}</kbd>
                </button>
                <button type="button" className="quick-action" onClick={then(openDesktopOverview)}>
                  <SquaresFour size={16} aria-hidden="true" />
                  <span className="quick-action-label">All desktops</span>
                  <kbd>{comboLabel('desktop-overview')}</kbd>
                </button>
                <button
                  type="button"
                  className="quick-action"
                  onClick={then(() => dispatchEvent(new Event(OPEN_SHORTCUTS)))}
                >
                  <Keyboard size={16} aria-hidden="true" />
                  <span className="quick-action-label">Keyboard shortcuts</span>
                  <kbd>?</kbd>
                </button>
              </section>

              <div className="quick-settings-foot">
                <button
                  type="button"
                  className="quick-link"
                  onClick={then(() => openSettings('appearance'))}
                >
                  Appearance settings
                </button>
                {lockable ? (
                  <button
                    type="button"
                    className="quick-link"
                    title={`Lock (${comboLabel('lock')})`}
                    onClick={then(() =>
                      api
                        .lockNow()
                        .then(applySecurity)
                        .catch((error) => setNote(error.message || 'Could not lock Vela.')),
                    )}
                  >
                    <LockSimple size={15} aria-hidden="true" />
                    Lock
                  </button>
                ) : null}
              </div>
              {note ? (
                <p className="quick-settings-note" role="alert">
                  {note}
                </p>
              ) : null}
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
