import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import Button from './ui/Button.jsx';
import WorkspaceHeader from './WorkspaceHeader.jsx';
import { useApps } from '../store.jsx';
import { readSession, writeSession } from '../storage.js';

// One workspace: an optional context panel, the contextual header, and the
// main surface. `scroll={false}` hands scrolling to the page itself, which Ask
// needs so its composer stays outside the transcript.
export default function WorkspacePage({
  title,
  subtitle,
  lead,
  actions,
  search = true,
  compactSearch = false,
  panel,
  scroll = true,
  className = '',
  children,
}) {
  const location = useLocation();
  const { apps, error, refreshApps } = useApps();
  const contentRef = useRef(null);
  const hasApps = Boolean(apps);

  useEffect(() => {
    const content = contentRef.current;
    if (!scroll || !content) return;
    if (location.state?.restoreLauncher && hasApps)
      content.scrollTop = Number(readSession(`vela.scroll.${location.pathname}`, 0));
    const save = () => writeSession(`vela.scroll.${location.pathname}`, content.scrollTop);
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
