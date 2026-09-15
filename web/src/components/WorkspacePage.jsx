import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import Button from './ui/Button.jsx';
import WorkspaceHeader from './WorkspaceHeader.jsx';
import { useShell } from '../shell-context.js';
import { useApps } from '../store.jsx';

// One workspace: an optional context panel, the contextual header, and the
// main surface. `scroll={false}` hands scrolling to the page itself, which Ask
// needs so its composer stays outside the transcript. `nav={false}` drops the
// phone drawer opener for a page whose own lead control opens the drawer; the
// shell drops it as well wherever the rail is already on screen, so Home never
// shows a hamburger beside its own navigation.
export default function WorkspacePage({
  title,
  subtitle,
  lead,
  actions,
  search = true,
  compactSearch = false,
  nav = true,
  panel,
  scroll = true,
  className = '',
  children,
}) {
  const location = useLocation();
  const { openNav, railFixed } = useShell();
  const { apps, error, refreshApps } = useApps();
  const contentRef = useRef(null);
  const hasApps = Boolean(apps);

  useEffect(() => {
    const content = contentRef.current;
    if (!scroll || !content) return;
    if (location.state?.restoreLauncher && hasApps)
      content.scrollTop = Number(sessionStorage.getItem(`vela.scroll.${location.pathname}`) || 0);
    const save = () =>
      sessionStorage.setItem(`vela.scroll.${location.pathname}`, String(content.scrollTop));
    content.addEventListener('scroll', save);
    return () => content.removeEventListener('scroll', save);
  }, [location.key, location.pathname, location.state?.restoreLauncher, hasApps, scroll]);

  return (
    <>
      {panel}
      <div className={`workspace-main${className ? ` ${className}` : ''}`}>
        <WorkspaceHeader
          lead={lead}
          title={title}
          subtitle={subtitle}
          actions={actions}
          search={search}
          compactSearch={compactSearch}
          onOpenNav={nav && !railFixed ? openNav : null}
        />
        <main
          className={`workspace-content${scroll ? '' : ' workspace-content-fixed'}`}
          ref={contentRef}
        >
          {error && (
            <div className="banner banner-error" role="alert">
              <div>
                <strong>Can’t connect to Vela.</strong>
                <p>Make sure the Vela server is running on this computer, then try again.</p>
              </div>
              <Button onClick={() => refreshApps()}>Retry</Button>
            </div>
          )}
          {children}
        </main>
      </div>
    </>
  );
}
