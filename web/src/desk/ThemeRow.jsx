import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, Download, Trash, UploadSimple } from '@phosphor-icons/react';
import { api } from '../api.js';
import Button from '../components/ui/Button.jsx';
import { getTheme, setThemeDocument, STOCK } from '../theme.js';
import { BUNDLED_WALLPAPERS } from './wallpaper.js';

/**
 * Picking, importing, exporting and removing a theme.
 *
 * A theme decides what light and dark are made of; the light/dark choice itself
 * stays where it was, in Settings, because it is a different decision and
 * people make it for a different reason.
 *
 * Nothing here fetches a theme from anywhere. Import reads a file the user
 * chose, in this browser, and sends its contents to be checked. That is the
 * whole distribution story on purpose: a theme that could arrive over the
 * network is a theme somebody could push.
 */
const MAX_BYTES = 32 * 1024;

const wallpaperName = (id) => BUNDLED_WALLPAPERS.find((wall) => wall.id === id)?.name || id;

export default function ThemeRow({ wallpaper, onUseWallpaper }) {
  const [themes, setThemes] = useState(null);
  const [selected, setSelected] = useState(STOCK);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState('');
  const [review, setReview] = useState(null);
  const file = useRef(null);

  const load = useCallback(async () => {
    const body = await api.getThemes();
    setThemes(body.themes);
    setSelected(body.selected || STOCK);
  }, []);

  useEffect(() => {
    let cancelled = false;
    api
      .getThemes()
      .then((body) => {
        if (cancelled) return;
        setThemes(body.themes);
        setSelected(body.selected || STOCK);
      })
      .catch(() => {
        if (!cancelled) setThemes([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /**
   * Apply first, then save. The dashboard repaints the moment a swatch is
   * pressed, as the wallpaper picker does; if the server refuses, the previous
   * theme goes back on rather than leaving the screen and the setting
   * disagreeing.
   */
  const pick = async (slug) => {
    if (busy || slug === selected) return;
    const previous = selected;
    setBusy(true);
    setFailed('');
    setSelected(slug);
    try {
      const document = slug === STOCK ? null : await api.getTheme(slug);
      setThemeDocument(document, getTheme());
      await api.updateSettings({ theme_id: slug });
    } catch (error) {
      setSelected(previous);
      const back = previous === STOCK ? null : await api.getTheme(previous).catch(() => null);
      setThemeDocument(back, getTheme());
      setFailed(error.message || 'Could not change the theme.');
    } finally {
      setBusy(false);
    }
  };

  /** Read the chosen file here and show what it would do before doing it. */
  const choose = async (event) => {
    const chosen = event.target.files?.[0];
    event.target.value = '';
    if (!chosen) return;
    setFailed('');
    if (chosen.size > MAX_BYTES) {
      setFailed(`A theme file is at most ${MAX_BYTES / 1024} KB.`);
      return;
    }
    try {
      const parsed = JSON.parse(await chosen.text());
      setReview({ document: parsed, name: chosen.name });
    } catch {
      setFailed('That file is not a Vela theme: it is not JSON.');
    }
  };

  const confirmImport = async (replace = false) => {
    setBusy(true);
    setFailed('');
    try {
      const body = await api.importTheme(review.document, { replace });
      setReview(null);
      await load();
      await pick(body.theme.slug);
    } catch (error) {
      setFailed(error.message || 'Could not import that theme.');
    } finally {
      setBusy(false);
    }
  };

  const remove = async (slug) => {
    setBusy(true);
    setFailed('');
    try {
      const body = await api.removeTheme(slug);
      if (body.selected) {
        setSelected(body.selected);
        setThemeDocument(null, getTheme());
      }
      await load();
    } catch (error) {
      setFailed(error.message || 'Could not remove that theme.');
    } finally {
      setBusy(false);
    }
  };

  if (themes === null) return <p className="vela-empty">Loading themes…</p>;

  const current = themes.find((theme) => theme.slug === selected);
  const suggested = current?.suggests?.wallpaper;

  return (
    <>
      <div className="personalise-themes" role="group" aria-label="Theme">
        {themes.map((theme) => (
          <button
            key={theme.slug}
            type="button"
            className={`personalise-theme${theme.slug === selected ? ' is-selected' : ''}`}
            data-theme-slug={theme.slug}
            disabled={busy}
            aria-pressed={theme.slug === selected}
            onClick={() => pick(theme.slug)}
          >
            <span className="personalise-theme-strip" aria-hidden="true">
              {theme.swatches.map((colour, index) => (
                <span
                  key={index}
                  className="personalise-theme-swatch"
                  // The one inline style here: a swatch is the theme's own
                  // colour, which no stylesheet can hold because it arrived
                  // with the theme.
                  style={{ background: colour }}
                />
              ))}
            </span>
            <span className="personalise-theme-name">
              {theme.name}
              {theme.slug === selected && <Check size={14} weight="bold" />}
            </span>
            {theme.imported ? <span className="personalise-theme-tag">Imported</span> : null}
          </button>
        ))}
      </div>

      {suggested ? (
        <p className="panel-note">
          Suggested wallpaper: {wallpaperName(suggested)}
          {wallpaper === suggested ? null : (
            <>
              {' · '}
              <button
                type="button"
                className="link-button"
                disabled={busy}
                onClick={() => onUseWallpaper?.(suggested)}
              >
                Use it
              </button>
            </>
          )}
        </p>
      ) : null}

      <div className="form-actions">
        <Button disabled={busy} onClick={() => file.current?.click()}>
          <UploadSimple size={15} aria-hidden="true" />
          Import a theme…
        </Button>
        {current?.imported ? (
          <>
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => {
                window.location.href = api.themeExportUrl(current.slug);
              }}
            >
              <Download size={15} aria-hidden="true" />
              Export
            </Button>
            <Button variant="ghost" disabled={busy} onClick={() => remove(current.slug)}>
              <Trash size={15} aria-hidden="true" />
              Remove
            </Button>
          </>
        ) : null}
      </div>

      <input
        ref={file}
        type="file"
        className="sr-only"
        accept="application/json,.json"
        aria-label="Choose a theme file"
        onChange={choose}
      />

      {review ? (
        <div className="personalise-review" role="group" aria-label="Review this theme">
          <p className="personalise-review-name">
            <strong>{review.document?.name || review.name}</strong>
            {review.document?.author ? ` · by ${review.document.author}` : null}
          </p>
          {review.document?.description ? (
            <p className="panel-note">{review.document.description}</p>
          ) : null}
          <p className="panel-note">
            {countTokens(review.document)} colours, {(review.document?.bases || []).join(' and ')}.
            Nothing else in it is applied: a theme sets colours, lengths, shadows and fonts from
            Vela&rsquo;s own list, and nothing that could load anything.
          </p>
          <div className="form-actions">
            <Button variant="primary" pending={busy} onClick={() => confirmImport(false)}>
              Add this theme
            </Button>
            <Button variant="ghost" disabled={busy} onClick={() => confirmImport(true)}>
              Replace if it exists
            </Button>
            <Button variant="ghost" disabled={busy} onClick={() => setReview(null)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}

      {failed ? (
        <p className="vela-empty vela-empty-error" role="alert">
          {failed}
        </p>
      ) : null}
    </>
  );
}

function countTokens(document) {
  const bases = Object.values(document?.tokens || {});
  return bases.reduce((total, tokens) => total + Object.keys(tokens || {}).length, 0);
}
