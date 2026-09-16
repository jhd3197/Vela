import { useEffect, useMemo } from 'react';
import { useApps } from '../store.jsx';
import useMediaQuery from '../hooks/useMediaQuery.js';
import { PHONE } from '../breakpoints.js';
import WorkspacePage from '../components/WorkspacePage.jsx';
import GlobalSearch from '../components/GlobalSearch.jsx';
import DeskGrid from '../desk/grid/DeskGrid.jsx';
import { DeskDataProvider } from '../desk/DeskDataProvider.jsx';
import { useWidgetTypes } from '../desk/registry.js';
import { colsOf, defaultBoards, repairBoard, widgetsOf } from '../desk/boards.js';

// The desk is what `/` is: the user's own apps and information over their own
// wallpaper, not a dashboard about the server. Widgets are host-rendered and
// only ever draw data Vela actually has; the wallpaper is a real image the
// user can replace, and the rail beside it is the same rail as everywhere else.
export default function Desk() {
  const { apps } = useApps();
  const phone = useMediaQuery(PHONE);
  const boardKey = phone ? 'phone' : 'desktop';
  const types = useWidgetTypes(apps);

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

  // Stage 1 keeps the boards in memory. They move to `<data_dir>/desk.json`
  // with Arrange mode.
  const boards = useMemo(() => defaultBoards(), []);
  const cols = colsOf(boards, boardKey);
  const knownTypes = useMemo(() => types.map((type) => type.id), [types]);
  const widgets = useMemo(
    () => repairBoard(widgetsOf(boards, boardKey), cols, knownTypes),
    [boards, boardKey, cols, knownTypes],
  );

  return (
    <WorkspacePage search={false} className="desk-workspace">
      <DeskDataProvider>
        <div className="desk">
          <div className="desk-top">
            <GlobalSearch />
          </div>
          <DeskGrid
            widgets={widgets}
            types={types}
            cols={cols}
            rowHeight={phone ? 120 : 150}
            gap={phone ? 12 : 16}
            edit={false}
          />
        </div>
      </DeskDataProvider>
    </WorkspacePage>
  );
}
