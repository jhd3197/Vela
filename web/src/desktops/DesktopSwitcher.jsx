// Which desktop you are looking at, and how to get to another one.
//
// It lives in the rail because the rail is the one piece of chrome that is
// there at every width and under every layout. A selector that disappeared
// when a window was maximized would be a selector you cannot trust.
//
// Choosing a desktop changes only this browser. Creating, renaming and deleting
// change the server, so they are confirmed and they say what they will do.
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Check, Monitor, Pencil, Plus, Robot, Trash } from '@phosphor-icons/react';
import { useDesktops } from './DesktopsProvider.jsx';
import useAttention, { attentionWord } from './useAttention.js';
import Button from '../components/ui/Button.jsx';
import Dialog from '../components/ui/Dialog.jsx';
import { useConfirm } from '../hooks/useConfirm.js';

/** The rail entry: the current desktop, and a menu of the others. */
export default function DesktopSwitcher({ onNavigate }) {
  const { desktops, selected, selectedId, select, views, remove } = useDesktops();
  const confirm = useConfirm();
  // What every agent desktop is doing, including the ones you are not looking
  // at. A desktop working in the background is the whole point of the feature,
  // so it has to be visible from anywhere.
  const attention = useAttention();
  const [open, setOpen] = useState(false);
  const [dialog, setDialog] = useState(null); // { kind: 'create' | 'rename' | 'delete' }
  const trigger = useRef(null);
  const menuId = useId();

  const close = useCallback(() => {
    setOpen(false);
    trigger.current?.focus();
  }, []);

  // Until the list has arrived there is nothing true to name, and a switcher
  // that said "Desktop" and then changed would be worse than one that waited.
  // A server with no desktops has nothing to switch between, so it draws
  // nothing rather than an empty menu — the same rule the rail avatar follows.
  if (!desktops.length) return null;

  // A summary of the desktops that are *not* in front of you. The one you are
  // looking at shows its own state in its own window; this is for the others.
  const others = Object.entries(attention).filter(([id]) => id !== selectedId);
  const needing = others.filter(([, entry]) => entry.needsYou).length;
  const working = others.filter(([, entry]) => entry.working).length;
  const elsewhereMark = needing ? 'attention' : working ? 'working' : null;
  const elsewhere = needing
    ? `${needing} other desktop${needing === 1 ? '' : 's'} need you`
    : working
      ? `${working} other desktop${working === 1 ? '' : 's'} working`
      : '';

  const choose = (id) => {
    select(id);
    setOpen(false);
    onNavigate?.();
    trigger.current?.focus();
  };

  return (
    <div className="rail-desktops">
      <button
        ref={trigger}
        type="button"
        className={`rail-item rail-desktop-item${open ? ' rail-item-active' : ''}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="rail-desktop-mark" aria-hidden="true">
          <Monitor size={19} weight="fill" />
        </span>
        <span className="rail-tip">{selected ? selected.name : 'Desktops'}</span>
        <span className="sr-only">
          {selected ? `${selected.name} — choose a desktop` : 'Choose a desktop'}
          {elsewhere ? `. ${elsewhere}` : ''}
        </span>
        {elsewhereMark && (
          <span className="rail-desktop-badge" data-kind={elsewhereMark} aria-hidden="true" />
        )}
      </button>

      {open && (
        <DesktopMenu
          id={menuId}
          desktops={desktops}
          selectedId={selectedId}
          attention={attention}
          onChoose={choose}
          onOpenAgent={async (desktop) => {
            // Opening the Agent window is an owner action on the desktop it
            // names, so choosing one and opening its window are the same gesture
            // rather than two that can disagree.
            if (desktop.id !== selectedId) select(desktop.id);
            setOpen(false);
            await views?.open?.({ kind: 'agent' });
            onNavigate?.();
          }}
          onClose={close}
          onAction={async (kind, desktop) => {
            setOpen(false);
            if (kind !== 'delete') {
              setDialog({ kind, desktop });
              return;
            }
            // Deleting asks one question and does the work behind the answer,
            // so it uses the dashboard's confirmation rather than a dialog of
            // its own. Create and rename need a field, so they keep theirs.
            await confirm({
              title: `Delete ${desktop.name}?`,
              message:
                'Its widgets, arrangement and wallpaper go. Your apps and everything they have ' +
                'saved stay exactly where they are — they are shared by every desktop.',
              confirmText: 'Delete desktop',
              pendingText: 'Deleting…',
              onConfirm: () => remove(desktop.id),
            });
            trigger.current?.focus();
          }}
        />
      )}

      {dialog && (
        <DesktopDialog
          request={dialog}
          onClose={() => {
            setDialog(null);
            trigger.current?.focus();
          }}
        />
      )}
    </div>
  );
}

/** The list itself. Escape closes it, and focus goes back to what opened it. */
function DesktopMenu({
  id,
  desktops,
  selectedId,
  attention,
  onChoose,
  onClose,
  onAction,
  onOpenAgent,
}) {
  const panel = useRef(null);

  useEffect(() => {
    // Focus the selected entry, so the keyboard lands where the eye already is.
    const current =
      panel.current?.querySelector('[data-selected="true"]') ||
      panel.current?.querySelector('button');
    current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (event) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
      }
    };
    const onPointer = (event) => {
      if (!panel.current?.contains(event.target)) onClose();
    };
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('pointerdown', onPointer, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      document.removeEventListener('pointerdown', onPointer, true);
    };
  }, [onClose]);

  const onKeyDown = (event) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    event.preventDefault();
    const items = Array.from(panel.current?.querySelectorAll('.desktop-menu-choice') || []);
    const index = items.indexOf(document.activeElement);
    const next = event.key === 'ArrowDown' ? index + 1 : index - 1;
    items[(next + items.length) % items.length]?.focus();
  };

  return (
    <div
      id={id}
      ref={panel}
      className="desktop-menu"
      role="menu"
      aria-label="Desktops"
      onKeyDown={onKeyDown}
    >
      <p className="desktop-menu-head">Desktops</p>
      <ul className="desktop-menu-list">
        {desktops.map((desktop) => (
          <li key={desktop.id} className="desktop-menu-row">
            <button
              type="button"
              role="menuitemradio"
              aria-checked={desktop.id === selectedId}
              data-selected={desktop.id === selectedId}
              className="desktop-menu-choice"
              onClick={() => onChoose(desktop.id)}
            >
              <span className="desktop-menu-name">{desktop.name}</span>
              {attentionWord(attention[desktop.id]) && (
                <span
                  className="desktop-menu-state"
                  data-kind={attention[desktop.id]?.needsYou ? 'attention' : 'working'}
                >
                  {attentionWord(attention[desktop.id])}
                </span>
              )}
              {desktop.id === selectedId ? (
                <Check size={14} weight="bold" aria-hidden="true" />
              ) : null}
            </button>
            <span className="desktop-menu-actions">
              <button
                type="button"
                className="desktop-menu-action"
                aria-label={
                  desktop.kind === 'agent'
                    ? `Open the Agent window for ${desktop.name}`
                    : `Let an agent work in ${desktop.name}`
                }
                onClick={() => onOpenAgent(desktop)}
              >
                <Robot size={14} aria-hidden="true" />
              </button>
              <button
                type="button"
                className="desktop-menu-action"
                aria-label={`Rename ${desktop.name}`}
                onClick={() => onAction('rename', desktop)}
              >
                <Pencil size={14} aria-hidden="true" />
              </button>
              <button
                type="button"
                className="desktop-menu-action"
                aria-label={`Delete ${desktop.name}`}
                disabled={desktops.length <= 1}
                onClick={() => onAction('delete', desktop)}
              >
                <Trash size={14} aria-hidden="true" />
              </button>
            </span>
          </li>
        ))}
      </ul>
      <button type="button" className="desktop-menu-new" onClick={() => onAction('create')}>
        <Plus size={14} aria-hidden="true" />
        New desktop
      </button>
    </div>
  );
}

/** Create and rename: the two that need a field. Deleting uses useConfirm. */
function DesktopDialog({ request, onClose }) {
  const { create, rename } = useDesktops();
  const [name, setName] = useState(request.desktop?.name || '');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const titleId = useId();

  const submit = async (event) => {
    event?.preventDefault();
    setBusy(true);
    setNote('');
    try {
      if (request.kind === 'create') await create(name.trim() || undefined);
      else await rename(request.desktop.id, name.trim(), request.desktop.revision);
      onClose();
    } catch (error) {
      // A rename that lost a race is the common one: the list has already been
      // refreshed, so saying so and letting them try again is the whole fix.
      setNote(error.message || 'That did not work.');
      setBusy(false);
    }
  };

  const creating = request.kind === 'create';
  return (
    <Dialog open pending={busy} aria-labelledby={titleId} onClose={onClose}>
      <h2 id={titleId}>{creating ? 'New desktop' : `Rename ${request.desktop.name}`}</h2>
      <form onSubmit={submit}>
        <label className="field-label" htmlFor="desktop-name">
          Name
        </label>
        <input
          id="desktop-name"
          className="field"
          type="text"
          maxLength={60}
          autoFocus
          value={name}
          placeholder={creating ? 'Named for you if you leave this empty' : ''}
          disabled={busy}
          onChange={(event) => setName(event.target.value)}
        />
        {creating && (
          <p className="panel-note">
            A new desktop starts empty, with its own widgets and its own wallpaper. Your apps and
            their data are shared with every desktop.
          </p>
        )}
        {note && (
          <p className="inline-error" role="alert">
            {note}
          </p>
        )}
        <div className="form-actions">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            pending={busy}
            disabled={busy || (!creating && !name.trim())}
          >
            {busy ? 'Saving…' : creating ? 'Create' : 'Rename'}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
