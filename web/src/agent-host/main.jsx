// The page an agent desktop's browser actually loads.
//
// It hosts one app's frame and the bridge to it. It is deliberately not the
// dashboard and shares none of it: no rail, no navigation, no settings, no
// approval controls, no way to reach another app. A page saying "Approve" is
// never what authorizes an effect, and the surest way to keep that true is for
// the page the agent looks at not to have one.
//
// The session arrives in `window.__velaAgentSession`, put there by the worker
// before this page was navigated to. Not in the URL: an address is written down
// in more places than anybody intends, and a bearer token in one is a bearer
// token in a log. The app's own iframe is sandboxed and cross-document, so it
// cannot read this page's variables either.
import React, { useMemo, useRef, useState } from 'react';
import ReactDOM from 'react-dom/client';
import useAppFrame from '../desktops/view-lifecycle.js';
import './agent-host.scss';

/** What the worker left for this page, checked rather than trusted. */
function bootstrap() {
  const given = window.__velaAgentSession;
  // Read once and removed, so nothing later in the page's life can pick it up
  // by looking at a global.
  delete window.__velaAgentSession;
  if (!given || typeof given !== 'object') return null;
  if (typeof given.token !== 'string' || typeof given.appUrl !== 'string') return null;
  return given;
}

function AgentHost({ boot }) {
  const frame = useRef(null);
  const [ready, setReady] = useState(false);
  const contextRef = useRef(() => ({}));

  const session = useMemo(
    () => ({
      token: boot.token,
      installationId: boot.installationId,
      capabilities: boot.capabilities || [],
      unavailableCapabilities: boot.unavailableCapabilities || [],
    }),
    [boot],
  );

  const { error, setError, disconnect } = useAppFrame({
    appId: boot.appId,
    enabled: true,
    session,
    frameRef: frame,
    contextRef,
    onReady: () => setReady(true),
  });

  contextRef.current = () => ({
    installationId: session.installationId,
    protocol: 1,
    capabilities: session.capabilities,
    unavailableCapabilities: session.unavailableCapabilities,
    theme: boot.theme || 'dark',
    locale: boot.locale || 'en',
    // An app is told it is in an agent's window. It is not told anything about
    // the run, the desktop or the owner: what it needs is its own geometry.
    view: { surface: 'embedded', chrome: 'agent' },
    viewport: {
      width: frame.current?.clientWidth || window.innerWidth,
      height: frame.current?.clientHeight || window.innerHeight,
      visualHeight: frame.current?.clientHeight || window.innerHeight,
      insets: { top: 0, right: 0, bottom: 0, left: 0 },
      hostControl: null,
    },
  });

  return (
    <div className="agent-host" data-ready={ready ? 'yes' : 'no'}>
      <iframe
        ref={frame}
        className="agent-host-frame"
        src={boot.appUrl}
        title={boot.appName || boot.appId}
        // The same sandbox the dashboard uses. Making it same-origin would make
        // observing it easier and the boundary meaningless.
        sandbox="allow-scripts"
        referrerPolicy="no-referrer"
        onLoad={(event) => {
          if (event.currentTarget.dataset.loaded) {
            disconnect();
            setError('The app navigated away from its workspace.');
            return;
          }
          event.currentTarget.dataset.loaded = 'true';
        }}
        onError={() => setError('The app could not load.')}
      />
      {error ? (
        <p className="agent-host-state" role="alert" data-state="error">
          {error}
        </p>
      ) : null}
    </div>
  );
}

const boot = bootstrap();
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {boot ? (
      <AgentHost boot={boot} />
    ) : (
      // Reached by opening this page by hand. It says so rather than looking
      // broken, and there is nothing here to use without a session anyway.
      <p className="agent-host-state" data-state="idle">
        This window is opened by Vela.
      </p>
    )}
  </React.StrictMode>,
);
