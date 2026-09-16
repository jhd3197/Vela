# Desk grid engine

The board geometry, the frame chrome and the widget primitives in this folder
come from ServerKit, which shares this repository's owner and MIT licence:

`https://github.com/jhd3197/ServerKit` — `frontend/src/components/dashboard/`
and `frontend/src/hooks/`.

Each copied file keeps a short origin note in its header. The ServerKit shell
(sidebar, command palette, status bar, operations dock), its i18next, Radix and
lucide dependencies and its plugin contribution system are deliberately not
copied; Vela's own `ui/` components, Phosphor icons and app registry take their
place.

What lives here:

- `grid/layout.js` — pure board geometry, parameterised on the column count.
- `grid/DeskGrid.jsx` — the board host: measures itself, positions frames and
  drives pointer move/resize.
- `grid/WidgetFrame.jsx` — chrome around one widget; headerless in view mode.
- `registry.js` — widget type registry and title derivation.
- `widgets/primitives.jsx` — presentational pieces every widget draws with.
- `metrics.js` — pure number helpers (aggregate, delta, formatting).
- `editing/` — the undo/redo transaction store used by Arrange mode.
- `tick.js` — one visibility-aware poll tick for the whole desk.
