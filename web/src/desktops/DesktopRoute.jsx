// `/desktops/:desktopId` — a link straight to one workspace.
//
// Useful for a bookmark or a note to yourself; it is not a share link, because
// everything behind it still needs this server's sign-in. Following one selects
// that desktop for this browser and then shows the ordinary desk, so the URL is
// a way in rather than a second kind of desk page.
//
// An id that no longer names a desktop gets something to do rather than an
// empty board: the workspace may have been deleted from another device, and
// "it is gone, here is the one you have" is the honest answer.
import { useEffect } from 'react';
import { Link, useParams } from 'react-router-dom';
import Desk from '../pages/Desk.jsx';
import { useDesktops } from './DesktopsProvider.jsx';

export default function DesktopRoute() {
  const { desktopId } = useParams();
  const { desktops, loaded, selectedId, select } = useDesktops();
  const known = desktops.some((desktop) => desktop.id === desktopId);

  useEffect(() => {
    if (known && desktopId !== selectedId) select(desktopId);
  }, [known, desktopId, selectedId, select]);

  // Until the list has arrived, showing "not found" would be a guess.
  if (!loaded) return <Desk />;

  if (!known) {
    return (
      <div className="state-block" role="status">
        <h2>That desktop is not here</h2>
        <p>It may have been deleted, or the link may be for another Vela server.</p>
        <Link className="btn btn-primary" to="/">
          Go to your desk
        </Link>
      </div>
    );
  }

  // Selecting is an effect, so the first render after following the link is
  // still the previous desktop. Waiting one frame is better than drawing the
  // wrong board and then swapping it.
  if (desktopId !== selectedId) return null;
  return <Desk />;
}
