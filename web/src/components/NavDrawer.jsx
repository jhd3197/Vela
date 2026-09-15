import Drawer from './ui/Drawer.jsx';

// A page's own panel on a phone, sliding in from the edge beside the rail. The
// rail itself stays on screen at every width, so the drawer carries only the
// panel: Ask places its conversations here. Escape and the backdrop return
// focus to the opener.
export default function NavDrawer({ open, onClose, returnFocusRef, label, panel }) {
  if (!open) return null;

  return (
    <Drawer
      open
      onClose={onClose}
      returnFocusRef={returnFocusRef}
      panelClassName="drawer-nav"
      aria-label={label}
    >
      <div className="drawer-nav-panel">{panel}</div>
    </Drawer>
  );
}
