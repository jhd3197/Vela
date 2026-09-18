// The All apps overlay's context, apart from the overlay itself.
//
// `AppsOverlay.jsx` renders the Launchpad, and the Launchpad — like the rail,
// the desk widgets and the search field — opens apps. Everything that opens an
// app wants to close the grid behind it, so the hook that says whether the grid
// is up lives here rather than in the file that imports the grid. Otherwise the
// opener would have to import the thing it is being used by.
import { createContext, useContext } from 'react';

export const AppsOverlayContext = createContext(null);

/** `{ appsOpen, openApps, closeApps, toggleApps }` — no-ops outside a provider. */
export function useAppsOverlay() {
  return (
    useContext(AppsOverlayContext) || {
      appsOpen: false,
      openApps: () => {},
      closeApps: () => {},
      toggleApps: () => {},
    }
  );
}
