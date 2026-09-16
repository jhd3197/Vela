import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useBlocker, useNavigate } from 'react-router-dom';
import {
  ArrowCounterClockwise,
  ArrowClockwise,
  ArrowSquareOut,
  ArrowUUpLeft,
  Crop,
  DotsThreeOutline,
  PaintBrush,
  Plus,
  Trash,
} from '@phosphor-icons/react';
import { useApps } from '../store.jsx';
import useMediaQuery from '../hooks/useMediaQuery.js';
import { PHONE } from '../breakpoints.js';
import WorkspacePage from '../components/WorkspacePage.jsx';
import GlobalSearch from '../components/GlobalSearch.jsx';
import Button from '../components/ui/Button.jsx';
import Dialog from '../components/ui/Dialog.jsx';
import ContextMenu from '../components/ui/ContextMenu.jsx';
import useSwipe from '../hooks/useSwipe.js';
import DeskGrid from '../desk/grid/DeskGrid.jsx';
import { DeskDataProvider } from '../desk/DeskDataProvider.jsx';
import WidgetLibrary from '../desk/WidgetLibrary.jsx';
import WidgetOptions from '../desk/WidgetOptions.jsx';
import PersonaliseSheet from '../desk/PersonaliseSheet.jsx';
import useDeskBoards from '../desk/useDeskBoards.js';
import { DEFAULT_WALLPAPER, useWallpaperFlags } from '../desk/wallpaper.js';
import useEditingSession from '../desk/editing/useEditingSession.js';
import { appIdOfType, APP_WIDGET_SIZES, useWidgetTypes } from '../desk/registry.js';
import AppWidget from '../desk/widgets/AppWidget.jsx';
import {
  colsOf,
  defaultBoards,
  MAX_WIDGETS_PER_BOARD,
  widgetsOf,
  withWidgets,
} from '../desk/boards.js';
import { compact, findFreeSpot, nextWidgetId, pushDown } from '../desk/grid/layout.js';
import { api } from '../api.js';
import { useResource } from '../hooks/useResource.js';

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
  const { apps, openApp, pushToast } = useApps();
  const navigate = useNavigate();
  const phone = useMediaQuery(PHONE);
  // On a phone, a swipe up from the bottom edge opens the Launchpad.
  const swipeUp = useSwipe({ onUp: () => navigate('/apps', { state: { returnTo: '/' } }) });
  const boardKey = phone ? 'phone' : 'desktop';
  const types = useWidgetTypes(apps, AppWidget);
  const knownTypes = useMemo(() => types.map((type) => type.id), [types]);
  const { boards, revision, loaded, save } = useDeskBoards(knownTypes);

  // The desk's own preferences. The sheet writes them through the settings API
  // and hands back what it saved, so the wallpaper changes as soon as it is
  // chosen rather than on the next load.
  const loadSettings = useCallback((options) => api.getSettings(options), []);
  const { data: settings } = useResource(loadSettings);
  const [deskPrefs, setDeskPrefs] = useState(null);
  const desk = useMemo(
    () => deskPrefs || settings?.desk || { wallpaper: DEFAULT_WALLPAPER, dim: true, labels: true },
    [deskPrefs, settings],
  );

  // The wallpaper and the dim toggle belong to the whole shell, so they ride on
  // the same body element the desk flag does. The Launchpad floats over the same
  // picture and sets them the same way, which is why the rules live in
  // `wallpaper.js` rather than in either page.
  useWallpaperFlags(desk);

  const [edit, setEdit] = useState(false);
  const [selected, setSelected] = useState(null);
  const [library, setLibrary] = useState(false);
  const [options, setOptions] = useState(null);
  const [personalise, setPersonalise] = useState(false);
  const [announcement, setAnnouncement] = useState('');
  const [saving, setSaving] = useState(false);
  const [confirmLeave, setConfirmLeave] = useState(false);
  // The wallpaper menu (right-click / long-press / the top-right ⋯) and the
  // reset confirmation. The buttons that used to crowd the search field live
  // here now, like a desktop's own right-click menu.
  const [deskMenu, setDeskMenu] = useState(null);
  const [widgetMenu, setWidgetMenu] = useState(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const addButton = useRef(null);
  const deskMenuButton = useRef(null);

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

  // What every widget is told about the desk it is on.
  const deskContext = useMemo(() => ({ desk, phone }), [desk, phone]);

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

  // Reset the board being looked at back to the seed Vela ships. Only this
  // board changes; the other one (phone or desktop) is untouched.
  const resetDesk = useCallback(async () => {
    const seed = defaultBoards();
    const result = await save(withWidgets(boards, boardKey, seed[boardKey].widgets));
    if (!result.ok) pushToast(result.message || 'The desk changed elsewhere; reloaded.', 'error');
  }, [save, boards, boardKey, pushToast]);

  // The wallpaper menu, opened from the top-right ⋯ or a right-click / long
  // press on empty board. Reuses the shared ContextMenu.
  const deskMenuItems = [
    { label: 'Add widget', icon: Plus, onSelect: () => setLibrary(true) },
    {
      label: 'Arrange desk',
      icon: Crop,
      disabled: !loaded || revision === null,
      onSelect: () => setEdit(true),
    },
    { label: 'Personalise', icon: PaintBrush, onSelect: () => setPersonalise(true) },
    { separator: true },
    { label: 'Reset desk', icon: ArrowUUpLeft, onSelect: () => setConfirmReset(true) },
  ];
  const openDeskMenu = (x, y, opener) => {
    deskMenuButton.current = opener || null;
    setDeskMenu({ x, y });
  };

  // A widget's own right-click menu in view mode: open its app, resize it, or
  // remove it. Resize and remove save straight away, since view mode has no
  // Done button.
  const saveBoardNow = async (nextWidgets) => {
    const result = await save(withWidgets(boards, boardKey, nextWidgets));
    if (!result.ok) pushToast(result.message || 'The desk changed elsewhere; reloaded.', 'error');
  };
  const resizeWidget = (target, size) => {
    const [w, h] = APP_WIDGET_SIZES[size];
    const current = widgetsOf(boards, boardKey);
    saveBoardNow(
      compact(
        current.map((entry) =>
          entry.i === target.i ? { ...entry, w: Math.min(w, cols), h } : entry,
        ),
      ),
    );
  };
  const removeWidget = (target) =>
    saveBoardNow(compact(widgetsOf(boards, boardKey).filter((entry) => entry.i !== target.i)));

  const widgetMenuItems = (() => {
    if (!widgetMenu) return [];
    const target = widgetMenu.widget;
    const appId = appIdOfType(target.type);
    const items = [];
    if (appId)
      items.push({
        label: 'Open app',
        icon: ArrowSquareOut,
        onSelect: () => openApp(appId, { returnTo: '/' }),
      });
    items.push({ label: 'Small', icon: Crop, onSelect: () => resizeWidget(target, 's') });
    items.push({ label: 'Medium', icon: Crop, onSelect: () => resizeWidget(target, 'm') });
    items.push({ label: 'Large', icon: Crop, onSelect: () => resizeWidget(target, 'l') });
    items.push({ separator: true });
    items.push({
      label: 'Remove',
      icon: Trash,
      danger: true,
      onSelect: () => removeWidget(target),
    });
    return items;
  })();

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

  // The Ask toggle changes this board rather than a preference, so it saves
  // immediately: Personalise is not an arrange session with a Done button.
  const toggleAsk = async (on) => {
    const current = widgetsOf(boards, boardKey);
    const next = on
      ? [
          ...current,
          {
            i: nextWidgetId(current),
            type: 'ask',
            ...findFreeSpot(current, Math.min(2, cols), 1, cols),
            w: Math.min(2, cols),
            h: 1,
            cfg: {},
          },
        ]
      : current.filter((entry) => entry.type !== 'ask');
    const result = await save(withWidgets(boards, boardKey, compact(next)));
    if (!result.ok) pushToast(result.message || 'The desk changed elsewhere; reloaded.', 'error');
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
    const { clientX, clientY } = event;
    // On a frame the gesture arranges; on bare wallpaper it opens the wallpaper
    // menu — the same press, on the thing it is about.
    press.current = {
      x: clientX,
      y: clientY,
      timer: setTimeout(() => {
        press.current = null;
        if (frame) {
          setEdit(true);
          setSelected(frame.dataset.widget);
          setAnnouncement('Arranging the desk.');
        } else {
          openDeskMenu(clientX, clientY);
        }
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
    <WorkspacePage
      search={false}
      className="desk-workspace"
      actions={
        edit ? null : (
          <Button
            ref={deskMenuButton}
            size="icon"
            aria-label="Desk options"
            aria-haspopup="menu"
            onClick={(event) => {
              const box = event.currentTarget.getBoundingClientRect();
              openDeskMenu(box.right - 210, box.bottom + 6, event.currentTarget);
            }}
          >
            <DotsThreeOutline size={16} weight="fill" aria-hidden="true" />
          </Button>
        )
      }
    >
      <DeskDataProvider>
        <div
          className={`desk${edit ? ' desk-arranging' : ''}`}
          onKeyDown={onKeyDown}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endPress}
          onPointerCancel={endPress}
          onContextMenu={(event) => {
            // Right-click bare wallpaper opens the wallpaper menu, like a
            // desktop; a widget keeps its own menu.
            if (edit || event.target.closest?.('.desk-frame')) return;
            event.preventDefault();
            openDeskMenu(event.clientX, event.clientY);
          }}
        >
          {/* The command surface: one centred field over the wallpaper. This is a
              deliberate departure from the prototype's left-aligned search. The
              buttons that used to sit beside it moved to the wallpaper menu. */}
          <div className="desk-top">
            <GlobalSearch variant="hero" />
            {edit && (
              <div className="desk-top-actions">
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
              </div>
            )}
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
            ctx={deskContext}
            selectedId={selected}
            onSelect={setSelected}
            onChange={(next) => setWidgets(next)}
            onWidgetMenu={onWidgetMenu}
            onViewMenu={(widget, x, y) => setWidgetMenu({ widget, x, y })}
            empty={
              <p className="desk-empty">
                Your desk is empty. Use <b>Add widget</b> to put something on it.
              </p>
            }
          />
          {phone && !edit && (
            <div
              className="desk-swipe-up"
              aria-hidden="true"
              onPointerDown={(event) => {
                event.stopPropagation();
                swipeUp.onPointerDown(event);
              }}
              onPointerMove={swipeUp.onPointerMove}
              onPointerUp={swipeUp.onPointerUp}
              onPointerCancel={swipeUp.onPointerCancel}
            />
          )}
        </div>

        {library && (
          <WidgetLibrary
            types={types}
            cols={cols}
            onAdd={addWidget}
            onClose={() => setLibrary(false)}
          />
        )}

        {personalise && (
          <PersonaliseSheet
            desk={desk}
            askOn={widgets.some((entry) => entry.type === 'ask')}
            onChange={setDeskPrefs}
            onToggleAsk={toggleAsk}
            onClose={() => {
              setPersonalise(false);
              deskMenuButton.current?.focus({ preventScroll: true });
            }}
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

      <ContextMenu
        open={Boolean(deskMenu)}
        x={deskMenu?.x || 0}
        y={deskMenu?.y || 0}
        items={deskMenuItems}
        label="Desk options"
        returnFocusRef={deskMenuButton}
        onClose={() => setDeskMenu(null)}
      />

      <ContextMenu
        open={Boolean(widgetMenu)}
        x={widgetMenu?.x || 0}
        y={widgetMenu?.y || 0}
        items={widgetMenuItems}
        label="Widget options"
        onClose={() => setWidgetMenu(null)}
      />

      <Dialog
        open={confirmReset}
        aria-labelledby="desk-reset-title"
        onClose={() => setConfirmReset(false)}
      >
        <h2 id="desk-reset-title">Reset this desk?</h2>
        <p>
          This replaces the current layout with the one Vela ships. Your other board is unchanged.
        </p>
        <div className="form-actions">
          <Button
            variant="primary"
            onClick={async () => {
              setConfirmReset(false);
              await resetDesk();
            }}
          >
            Reset desk
          </Button>
          <Button onClick={() => setConfirmReset(false)}>Cancel</Button>
        </div>
      </Dialog>

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
