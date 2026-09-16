// One keyboard-shortcut owner for the whole shell. A single keydown listener
// on `window` drives the OS-level shortcuts (open the Launchpad, later the
// shortcut sheet and the pinned-app keys) so no page has to grow its own
// global handler. The listener ignores editable targets — inputs, textareas,
// selects and contenteditable — so a shortcut never fires while someone is
// typing, and it cannot see keys pressed inside an app's iframe because those
// events are delivered to the frame's own document, not to this window.
import { useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

const LAUNCHPAD = '/apps';

// Where the user was before the Launchpad opened, so Escape and a second
// Ctrl+Space put them back rather than guessing at `/`.
let launchpadReturn = '/';

export function launchpadReturnTo() {
  return launchpadReturn || '/';
}

function isEditable(target) {
  if (!target) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return Boolean(target.isContentEditable);
}

// Ctrl+Space (Cmd+Space on a Mac) toggles the Launchpad from anywhere. Space
// is `event.code === 'Space'`, which is stable across layouts and does not
// depend on the key producing a printable character.
function isLaunchpadToggle(event) {
  const withModifier = event.ctrlKey || event.metaKey;
  return withModifier && !event.altKey && !event.shiftKey && event.code === 'Space';
}

export function useGlobalShortcuts() {
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    const onKey = (event) => {
      if (event.defaultPrevented) return;
      // The Launchpad toggle is a deliberate modifier chord, so it works even
      // while a field has focus — including the Launchpad's own search box.
      if (isLaunchpadToggle(event)) {
        event.preventDefault();
        const here = location.pathname + location.search;
        if (location.pathname === LAUNCHPAD) {
          navigate(launchpadReturnTo());
        } else {
          launchpadReturn = here;
          navigate(LAUNCHPAD, { state: { returnTo: here } });
        }
        return;
      }
      // Plain-key shortcuts (added in later stages) must not fire while typing.
      if (isEditable(event.target)) return;
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [navigate, location.pathname, location.search]);
}
