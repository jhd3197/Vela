import { deskApi } from './useDeskBoards.js';
import { widgetsOf, withWidgets, colsOf } from './boards.js';
import { compact, findFreeSpot, nextWidgetId } from './grid/layout.js';
import { appWidgetTypeId, APP_WIDGET_SIZES } from './registry.js';

// The first widget an app declares, if any — what "Add widget to desk" places.
export function firstWidget(app) {
  const declared = Array.isArray(app?.widgets) ? app.widgets[0] : null;
  return declared && typeof declared.id === 'string' ? declared : null;
}

// Place an app's first declared widget on the desktop board and save it. Shared
// by the Launchpad and the app window's menu so "Add widget to desk" means the
// same thing wherever it is chosen. It lands on the desktop the user is looking
// at, which is why the caller has to say which one. Returns true when a widget
// was added.
export async function addAppWidgetToDesk(app, pushToast, desktopId) {
  const declared = firstWidget(app);
  if (!declared) return false;
  if (!desktopId) {
    pushToast?.('No desktop is selected yet.', 'error');
    return false;
  }
  try {
    const { revision, boards } = await deskApi.load(desktopId);
    const key = 'desktop';
    const current = widgetsOf(boards, key);
    const cols = colsOf(boards, key);
    const [w, h] = APP_WIDGET_SIZES[declared.size] || APP_WIDGET_SIZES.m;
    const width = Math.min(w, cols);
    const placed = {
      i: nextWidgetId(current),
      type: appWidgetTypeId(app.id, declared.id),
      ...findFreeSpot(current, width, h, cols),
      w: width,
      h,
      cfg: {},
    };
    await deskApi.save(
      desktopId,
      revision,
      withWidgets(boards, key, compact([...current, placed])),
    );
    pushToast?.(`Added ${app.name} to your desk.`, 'success');
    return true;
  } catch (error) {
    pushToast?.(error.message || 'Could not add the widget.', 'error');
    return false;
  }
}
