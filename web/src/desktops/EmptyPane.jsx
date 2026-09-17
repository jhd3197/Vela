// A half of a split with nothing in it.
//
// Deliberately a thing rather than the absence of one. When a split member is
// closed or minimized the pane stays, says it is empty and offers to be filled
// — because the alternative is a layout that silently rearranges itself around
// the gap, and somebody coming back to a minimized window would find its slot
// taken by whatever happened to be nearby.
//
// What it offers is what is already open first, then the apps this desktop can
// open. Filling it from an open window is a move, not a second copy: the same
// view, the same session, in a different place on the screen.
import { useMemo, useState } from 'react';
import { SquaresFour } from '@phosphor-icons/react';
import AppIcon from '../components/AppIcon.jsx';
import Button from '../components/ui/Button.jsx';

export default function EmptyPane({ side, bounds, openViews, apps, onChoose, onOpenApp, onExit }) {
  const [picking, setPicking] = useState(false);
  const choices = useMemo(() => (apps || []).filter((app) => app.installed).slice(0, 24), [apps]);

  return (
    <section
      className="empty-pane"
      style={{
        left: `${bounds.x}px`,
        top: `${bounds.y}px`,
        width: `${bounds.width}px`,
        height: `${bounds.height}px`,
      }}
      aria-label={`Empty ${side === 'left' ? 'left' : 'right'} pane`}
    >
      {!picking ? (
        <div className="empty-pane-body">
          <SquaresFour size={28} weight="duotone" aria-hidden="true" />
          <p>Nothing on this side yet.</p>
          <div className="form-actions">
            <Button size="small" onClick={() => setPicking(true)}>
              Choose something
            </Button>
            <Button size="small" variant="ghost" onClick={onExit}>
              Leave split view
            </Button>
          </div>
        </div>
      ) : (
        <div className="empty-pane-picker">
          {openViews.length > 0 && (
            <>
              <h3>Already open</h3>
              <ul>
                {openViews.map((view) => (
                  <li key={view.id}>
                    <button
                      type="button"
                      onClick={() => {
                        setPicking(false);
                        onChoose(view.id);
                      }}
                    >
                      {view.label}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
          <h3>Open an app</h3>
          <ul className="empty-pane-apps">
            {choices.map((app) => (
              <li key={app.id}>
                <button
                  type="button"
                  onClick={() => {
                    setPicking(false);
                    onOpenApp(app.id);
                  }}
                >
                  <AppIcon app={app} size={22} />
                  <span>{app.name}</span>
                </button>
              </li>
            ))}
          </ul>
          <Button size="small" variant="ghost" onClick={() => setPicking(false)}>
            Cancel
          </Button>
        </div>
      )}
    </section>
  );
}
