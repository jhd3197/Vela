// The browser tab follows what has focus.
//
// Mounted once, in the shell, so it holds at every width — the top bar is not
// drawn on a phone, but a phone's tab switcher still shows a title. It resolves
// focus the same way the bar does, so the two can never name different things.
import { useEffect, useMemo } from 'react';
import { useLocation, useMatch } from 'react-router-dom';
import { useDesktops } from '../desktops/DesktopsProvider.jsx';
import { dashboardPages } from '../navigation.js';
import { useApps } from '../store.jsx';
import { DOCUMENT_TITLE, documentTitleFor, resolveFocus } from './focus.js';

export default function useDocumentTitle() {
  const location = useLocation();
  const appRoute = useMatch('/app/:id');
  const { apps } = useApps();
  const { views } = useDesktops();

  const title = useMemo(
    () =>
      documentTitleFor(
        resolveFocus({
          pathname: location.pathname,
          views,
          apps,
          appId: appRoute?.params?.id || null,
          pages: dashboardPages,
        }),
      ),
    [location.pathname, appRoute?.params?.id, views, apps],
  );

  useEffect(() => {
    document.title = title;
  }, [title]);

  // Leaving the shell — signing out, an error page — gives the tab its own
  // name back rather than leaving the last window's on it.
  useEffect(
    () => () => {
      document.title = DOCUMENT_TITLE;
    },
    [],
  );
}
