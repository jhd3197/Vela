// Every desktop at once: its picture, its name, how many windows it has open
// and whether it needs you.
//
// The rail's desktop menu names the desktops; this shows them, the way an
// overview of workspaces does, so choosing one is recognising it rather than
// remembering what it was called. It opens from Alt+Shift+↑ and from the
// desktop menu, and anything can ask for it with the `vela:desktop-overview`
// event. Choosing a desktop shows its desk, the same as choosing it in the menu.
import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import Dialog from '../components/ui/Dialog.jsx';
import { useDesktops } from './DesktopsProvider.jsx';
import { desktopsApi } from './desktopsApi.js';
import useAttention, { attentionWord } from './useAttention.js';
import { BUNDLED_WALLPAPERS, resolveWallpaper } from '../desk/wallpaper.js';

export const OPEN_OVERVIEW = 'vela:desktop-overview';

/** Ask for the overview from anywhere: a menu, a shortcut, a button. */
export function openDesktopOverview() {
  dispatchEvent(new Event(OPEN_OVERVIEW));
}

/** The thumbnail a desktop's wallpaper draws with, as a style, or nothing for a gradient. */
function thumbStyle(desktopId, look) {
  if (!look) return undefined;
  if (look.wallpaper === 'custom') {
    return {
      backgroundImage: `url('${desktopsApi.wallpaperUrl(desktopId)}?v=${look.revision ?? 0}')`,
    };
  }
  // No stored choice is the default picture, and Daily is today's.
  const { id } = resolveWallpaper(look);
  if (!BUNDLED_WALLPAPERS.some((wall) => wall.id === id)) return undefined;
  return { backgroundImage: `url('/wallpapers/thumbs/${id}.jpg')` };
}

export default function DesktopOverview() {
  const { desktops, selectedId, select } = useDesktops();
  const navigate = useNavigate();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  // What each desktop looks like and holds, fetched when the overview opens:
  // it is a glance at the moment it is asked for, not something to keep polling.
  const [details, setDetails] = useState({});
  const attention = useAttention({ enabled: open });
  const current = useRef(null);
  const list = useRef(null);

  useEffect(() => {
    const toggle = () => setOpen((value) => !value);
    addEventListener(OPEN_OVERVIEW, toggle);
    return () => removeEventListener(OPEN_OVERVIEW, toggle);
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    let live = true;
    Promise.all(
      desktops.map(async (desktop) => {
        const [look, views] = await Promise.all([
          desktopsApi.appearance(desktop.id).catch(() => null),
          desktopsApi.views(desktop.id).catch(() => null),
        ]);
        return [
          desktop.id,
          { look, windows: Array.isArray(views?.views) ? views.views.length : null },
        ];
      }),
    ).then((entries) => {
      if (live) setDetails(Object.fromEntries(entries));
    });
    return () => {
      live = false;
    };
  }, [open, desktops]);

  const close = useCallback(() => setOpen(false), []);

  const choose = (id) => {
    setOpen(false);
    if (id !== selectedId) select(id);
    if (location.pathname !== '/') navigate('/');
  };

  // The cards are a grid; arrows move along it, wrapping, as the Launchpad's
  // tiles do. Tab still leaves for the dialog's own close button.
  const onKeyDown = (event) => {
    const cards = Array.from(list.current?.querySelectorAll('.desktop-card') || []);
    const index = cards.indexOf(document.activeElement);
    if (index === -1) return;
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    if (!step) return;
    event.preventDefault();
    cards[(index + step + cards.length) % cards.length]?.focus();
  };

  if (!desktops.length) return null;

  return (
    <Dialog
      open={open}
      onClose={close}
      closeOnBackdrop
      initialFocusRef={current}
      className="modal-dialog desktop-overview"
      aria-labelledby="desktop-overview-title"
    >
      <h2 id="desktop-overview-title">Desktops</h2>
      <ul className="desktop-overview-list" ref={list} onKeyDown={onKeyDown}>
        {desktops.map((desktop) => {
          const here = desktop.id === selectedId;
          const detail = details[desktop.id];
          const word = attentionWord(attention[desktop.id]);
          const windows = detail?.windows;
          return (
            <li key={desktop.id}>
              <button
                ref={here ? current : undefined}
                type="button"
                className={`desktop-card${here ? ' is-current' : ''}`}
                aria-current={here ? 'true' : undefined}
                onClick={() => choose(desktop.id)}
              >
                <span
                  className="desktop-card-picture"
                  data-wallpaper={detail?.look ? resolveWallpaper(detail.look).id : undefined}
                  style={thumbStyle(desktop.id, detail?.look)}
                  aria-hidden="true"
                />
                <span className="desktop-card-name">{desktop.name}</span>
                <span className="desktop-card-meta">
                  {windows === null || windows === undefined
                    ? ' '
                    : windows === 0
                      ? 'No windows'
                      : `${windows} window${windows === 1 ? '' : 's'}`}
                  {word ? ` · ${word}` : ''}
                  {here ? ' · You are here' : ''}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </Dialog>
  );
}
