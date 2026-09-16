import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useBlocker } from 'react-router-dom';
import { ArrowCounterClockwise, ArrowClockwise, Crop, Plus } from '@phosphor-icons/react';
import { useApps } from '../store.jsx';
import useMediaQuery from '../hooks/useMediaQuery.js';
import { PHONE } from '../breakpoints.js';
import WorkspacePage from '../components/WorkspacePage.jsx';
import GlobalSearch from '../components/GlobalSearch.jsx';
import Button from '../components/ui/Button.jsx';
import Dialog from '../components/ui/Dialog.jsx';
import DeskGrid from '../desk/grid/DeskGrid.jsx';
import { DeskDataProvider } from '../desk/DeskDataProvider.jsx';
import WidgetLibrary from '../desk/WidgetLibrary.jsx';
import WidgetOptions from '../desk/WidgetOptions.jsx';
import useDeskBoards from '../desk/useDeskBoards.js';
import useEditingSession from '../desk/editing/useEditingSession.js';
import { useWidgetTypes } from '../desk/registry.js';
import { colsOf, MAX_WIDGETS_PER_BOARD, widgetsOf, withWidgets } from '../desk/boards.js';
import { compact, findFreeSpot, nextWidgetId, pushDown } from '../desk/grid/layout.js';

// How long a finger has to rest on a widget to start arranging, and how far it
// may drift first. Both match the gesture the phone shell already uses: long
// enough not to fire while scrolling, short enough to feel deliberate.
const LONG_PRESS_MS = 500;
const LONG_PRESS_SLOP = 8;

// The desk is what `/` is: the user's own apps and information over their own
// wallpaper, not a dashboard about the server. Widgets are host-rendered and
// only ever draw data Vela actually has; the wallpaper is a real image the
// user can replace, and the rail beside it is the same rail as everywhere else.
export default function Desk() {
  const { apps, pushToast } = useApps();
  const phone = useMediaQuery(PHONE);
  const boardKey = phone ? 'phone' : 'desktop';
  const types = useWidgetTypes(apps);
  const knownTypes = useMemo(() => types.map((type) => type.id), [types]);
  const { boards, revision, loaded, save } = useDeskBoards(knownTypes);

  const [edit, setEdit] = useState(false);
  const [selected, setSelected] = useState(null);
  const [library, setLibrary] = useState(false);
  const [options, setOptions] = useState(null);
  const [announcement, setAnnouncement] = useState('');
  const [saving, setSaving] = useState(false);
  const [confirmLeave, setConfirmLeave] = useState(false);
  const arrangeButton = useRef(null);
  const addButton = useRef(null);

  // The wallpaper belongs to the whole shell, not to this page's scroll box:
  // it has to sit behind the rail as well. A body flag is the least invasive
  // way to say "this route is the desk" without threading a prop through the
  // shell, and it is cleared on the way out so no other page inherits it.
  useEffect(() => {
    document.body.dataset.desk = 'on';
    return () => {
      delete document.body.dataset.desk;
    };
  }, []);

  // Arrange mode edits a draft of both boards. The baseline is what the server
  // last confirmed, so Cancel is "put it back" and undo/redo work across the
  // whole session rather than per widget.
  const session = useEditingSession({ baseline: boards });
  const { reset, transaction, undo, redo, canUndo, canRedo, isDirty, draft } = session;

  // A reload, or a save from another tab, replaces the baseline. Doing that
  // while someone is arranging would pull the board out from under them, so it
  // only happens outside Arrange mode.
  const editRef = useRef(edit);
  editRef.current = edit;
  useEffect(() => {
    if (!editRef.current) reset(boards);
  }, [boards, reset]);

  const shown = edit ? draft : boards;
  const cols = colsOf(shown, boardKey);
  const widgets = widgetsOf(shown, boardKey);

  const setWidgets = useCallback(
    (next, options = {}) => transaction((current) => withWidgets(current, boardKey, next), options),
    [transaction, boardKey],
  );

  const dirtyRef = useRef(false);
  dirtyRef.current = edit && isDirty;
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirtyRef.current && currentLocation.pathname !== nextLocation.pathname,
  );

  const leaveEdit = useCallback(() => {
    setEdit(false);
    setSelected(null);
    setAnnouncement('');
  }, []);

  const cancel = useCallback(() => {
    reset(boards);
    leaveEdit();
  }, [reset, boards, leaveEdit]);

  const done = useCallback(async () => {
    if (!isDirty) {
      leaveEdit();
      return true;
    }
    setSaving(true);
    const result = await save(draft);
    setSaving(false);
    if (result.ok) {
      // The saved draft is the new baseline. The reload effect cannot do this
      // for us: the boards it watches are replaced while edit mode is still on.
      reset(draft);
      leaveEdit();
      return true;
    }
    if (result.conflict) {
      if (result.boards) reset(result.boards);
      leaveEdit();
      pushToast('The desk changed elsewhere; reloaded.', 'error');
      return true;
    }
    pushToast(result.message, 'error');
    return false;
  }, [isDirty, save, draft, reset, leaveEdit, pushToast]);

  // --------------------------------------------------------------- editing

  const addWidget = (type) => {
    const width = Math.min(type.w, cols);
    if (widgets.length >= MAX_WIDGETS_PER_BOARD) {
      pushToast('This board is full.', 'error');
      return;
    }
    const spot = findFreeSpot(widgets, width, type.h, cols);
    const placed = {
      i: nextWidgetId(widgets),
      type: type.id,
      ...spot,
      w: width,
      h: type.h,
      cfg: { ...(type.defaultCfg || {}) },
    };
    setWidgets(compact([...widgets, placed]));
    setLibrary(false);
    setEdit(true);
    setSelected(placed.i);
    setAnnouncement(`${type.name} added.`);
  };

  const onWidgetMenu = (action, widget) => {
    if (action === 'remove') {
      setWidgets(compact(widgets.filter((entry) => entry.i !== widget.i)));
      setSelected(null);
      setAnnouncement('Widget removed.');
      return;
    }
    if (action === 'duplicate') {
      if (widgets.length >= MAX_WIDGETS_PER_BOARD) {
        pushToast('This board is full.', 'error');
        return;
      }
      const copy = {
        ...widget,
        i: nextWidgetId(widgets),
        ...findFreeSpot(widgets, widget.w, widget.h, cols),
      };
      setWidgets(compact([...widgets, copy]));
      setSelected(copy.i);
      setAnnouncement('Widget duplicated.');
      return;
    }
    if (action === 'config') setOptions(widget);
  };

  // Keyboard arrangement. A pointer gesture is not the only way to move a
  // widget: with a frame focused, arrows move it, Shift+arrows resize it and
  // Delete removes it, and every change is announced.
  const onKeyDown = (event) => {
    if (!edit) return;
    const frame = event.target.closest?.('.desk-frame');
    const id = frame?.dataset.widget;
    if (!id) return;
    const widget = widgets.find((entry) => entry.i === id);
    if (!widget) return;
    const step = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[
      event.key
    ];
    if (step) {
      event.preventDefault();
      const [dx, dy] = step;
      const next = event.shiftKey
        ? {
            ...widget,
            w: Math.max(1, Math.min(cols - widget.x, widget.w + dx)),
            h: Math.max(1, widget.h + dy),
          }
        : {
            ...widget,
            x: Math.max(0, Math.min(cols - widget.w, widget.x + dx)),
            y: Math.max(0, widget.y + dy),
          };
      setWidgets(pushDown(widgets, next), { coalesceKey: `key:${id}` });
      setAnnouncement(
        event.shiftKey ? `${next.w} by ${next.h}.` : `Column ${next.x + 1}, row ${next.y + 1}.`,
      );
      return;
    }
    if (event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault();
      setWidgets(compact(widgets.filter((entry) => entry.i !== id)));
      setSelected(null);
      setAnnouncement('Widget removed.');
    }
  };

  // Ctrl+Z / Ctrl+Shift+Z while arranging, so the keyboard path is the one
  // people already have in their fingers.
  useEffect(() => {
    if (!edit) return undefined;
    const onKey = (event) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 'z') return;
      event.preventDefault();
      if (event.shiftKey) redo();
      else undo();
    };
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  }, [edit, undo, redo]);

  // Long-press on a widget enters Arrange mode on a phone, where there is no
  // hover and the toolbar buttons are a reach away. Cancelled by any real
  // movement, so it never fires while scrolling the board.
  const press = useRef(null);
  const onPointerDown = (event) => {
    if (edit || event.pointerType === 'mouse') return;
    const frame = event.target.closest?.('.desk-frame');
    if (!frame) return;
    const { clientX, clientY } = event;
    press.current = {
      x: clientX,
      y: clientY,
      id: frame.dataset.widget,
      timer: setTimeout(() => {
        press.current = null;
        setEdit(true);
        setSelected(frame.dataset.widget);
        setAnnouncement('Arranging the desk.');
      }, LONG_PRESS_MS),
    };
  };
  const endPress = () => {
    if (press.current) clearTimeout(press.current.timer);
    press.current = null;
  };
  const onPointerMove = (event) => {
    const started = press.current;
    if (!started) return;
    if (
      Math.abs(event.clientX - started.x) > LONG_PRESS_SLOP ||
      Math.abs(event.clientY - started.y) > LONG_PRESS_SLOP
    ) {
      endPress();
    }
  };
  useEffect(() => endPress, []);

  const busy = saving;
  const blocked = blocker.state === 'blocked';

  return (
    <WorkspacePage search={false} className="desk-workspace">
      <DeskDataProvider>
        <div
          className={`desk${edit ? ' desk-arranging' : ''}`}
          onKeyDown={onKeyDown}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endPress}
          onPointerCancel={endPress}
        >
          <div className="desk-top">
            <GlobalSearch />
            <div className="desk-top-actions">
              {edit ? (
                <>
                  <Button size="icon" aria-label="Undo" disabled={!canUndo || busy} onClick={undo}>
                    <ArrowCounterClockwise size={16} aria-hidden="true" />
                  </Button>
                  <Button size="icon" aria-label="Redo" disabled={!canRedo || busy} onClick={redo}>
                    <ArrowClockwise size={16} aria-hidden="true" />
                  </Button>
                  <Button ref={addButton} disabled={busy} onClick={() => setLibrary(true)}>
                    <Plus size={15} aria-hidden="true" />
                    Add widget
                  </Button>
                  <Button disabled={busy} onClick={cancel}>
                    Cancel
                  </Button>
                  <Button variant="primary" pending={busy} onClick={done}>
                    Done
                  </Button>
                </>
              ) : (
                <>
                  <Button ref={addButton} disabled={!loaded} onClick={() => setLibrary(true)}>
                    <Plus size={15} aria-hidden="true" />
                    Add widget
                  </Button>
                  <Button
                    ref={arrangeButton}
                    disabled={!loaded || revision === null}
                    onClick={() => setEdit(true)}
                  >
                    <Crop size={15} aria-hidden="true" />
                    Arrange desk
                  </Button>
                </>
              )}
            </div>
          </div>
          <p className="sr-only" role="status" aria-live="polite">
            {announcement}
          </p>
          <DeskGrid
            widgets={widgets}
            types={types}
            cols={cols}
            rowHeight={phone ? 120 : 150}
            gap={phone ? 12 : 16}
            edit={edit}
            selectedId={selected}
            onSelect={setSelected}
            onChange={(next) => setWidgets(next)}
            onWidgetMenu={onWidgetMenu}
            empty={
              <p className="desk-empty">
                Your desk is empty. Use <b>Add widget</b> to put something on it.
              </p>
            }
          />
        </div>

        {library && (
          <WidgetLibrary
            types={types}
            cols={cols}
            onAdd={addWidget}
            onClose={() => setLibrary(false)}
          />
        )}

        {options && (
          <WidgetOptions
            widget={options}
            onClose={() => setOptions(null)}
            onSave={(cfg) =>
              setWidgets(
                widgets.map((entry) => (entry.i === options.i ? { ...entry, cfg } : entry)),
              )
            }
          />
        )}
      </DeskDataProvider>

      <Dialog
        open={confirmLeave || blocked}
        pending={busy}
        aria-labelledby="desk-unsaved-title"
        onClose={() => {
          setConfirmLeave(false);
          if (blocked) blocker.reset();
        }}
      >
        <h2 id="desk-unsaved-title">Keep your changes to the desk?</h2>
        <p>You have moved things around without saving.</p>
        <div className="form-actions">
          <Button
            variant="primary"
            pending={busy}
            onClick={async () => {
              if (await done()) {
                setConfirmLeave(false);
                if (blocked) blocker.proceed();
              }
            }}
          >
            Save and leave
          </Button>
          <Button
            disabled={busy}
            onClick={() => {
              cancel();
              setConfirmLeave(false);
              if (blocked) blocker.proceed();
            }}
          >
            Discard
          </Button>
          <Button
            disabled={busy}
            onClick={() => {
              setConfirmLeave(false);
              if (blocked) blocker.reset();
            }}
          >
            Cancel
          </Button>
        </div>
      </Dialog>
    </WorkspacePage>
  );
}
