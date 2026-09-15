import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  createMemoryRouter,
  Outlet,
  RouterProvider,
  Route,
  createRoutesFromElements,
} from 'react-router-dom';
import { dashboardPages } from '../../src/navigation.js';
import { AppsProvider } from '../../src/store.jsx';
import { EngineProvider } from '../../src/engine.jsx';
import SettingsProvider from '../../src/components/SettingsProvider.jsx';
import Shell from '../../src/components/Shell.jsx';
import WorkspacePage from '../../src/components/WorkspacePage.jsx';
import ChatComposer from '../../src/components/ChatComposer.jsx';
import Dialog from '../../src/components/ui/Dialog.jsx';
import Drawer from '../../src/components/ui/Drawer.jsx';
import FormField from '../../src/components/ui/FormField.jsx';
import { sharedViewport } from '../../src/viewport.js';
import '../../src/styles/main.scss';

// Browser-only layout fixture: the real shell, the real composer, the real
// dialog and drawer, with disposable app records from a stubbed fetch. It
// never reaches a Vela server or a user's installed apps.
//
// A headless browser has no on-screen keyboard and no pinch gesture, so the
// visual viewport is replaced with a controllable stand-in before the shared
// viewport service starts. The service, its geometry model and every layout
// rule that reads its variables are the real ones; only the phone hardware is
// simulated. Physical keyboard behaviour still has to be accepted on a device.
const visual = new EventTarget();
Object.assign(visual, {
  offsetLeft: 0,
  offsetTop: 0,
  width: window.innerWidth,
  height: window.innerHeight,
  scale: 1,
});
Object.defineProperty(window, 'visualViewport', { value: visual, configurable: true });

// While nothing is being simulated the stand-in simply follows the window.
let simulating = false;
const announce = () => visual.dispatchEvent(new Event('resize'));
addEventListener('resize', () => {
  if (simulating) return;
  visual.width = window.innerWidth;
  visual.height = window.innerHeight;
  announce();
});

window.fixture = {
  // Occlude the bottom of the visible rectangle, the way a keyboard does.
  keyboard(height) {
    simulating = true;
    visual.height = window.innerHeight - height;
    announce();
  },
  // Pinch zoom shrinks the same rectangle and offsets it, occluding nothing.
  zoom(scale, offsetTop = 0) {
    simulating = true;
    visual.scale = scale;
    visual.height = window.innerHeight / scale;
    visual.width = window.innerWidth / scale;
    visual.offsetTop = offsetTop;
    announce();
  },
  reset() {
    simulating = false;
    visual.scale = 1;
    visual.offsetTop = 0;
    visual.width = window.innerWidth;
    visual.height = window.innerHeight;
    announce();
  },
};

sharedViewport();

const apps = [
  { id: 'notes', name: 'Notes', color: '#7c4dee' },
  { id: 'health', name: 'Health', color: '#21a377' },
  { id: 'meals', name: 'Meals', color: '#f0a93c' },
  { id: 'finance', name: 'Finance', color: '#2bb6d8' },
  { id: 'system', name: 'System info', color: '#9184d9' },
].map((app) => ({
  version: '1.0.0',
  category: 'utilities',
  supported: true,
  installed: true,
  running: true,
  schemaVersion: 2,
  ...app,
}));

const real = window.fetch.bind(window);
window.fetch = async (input, init) => {
  const url = String(input instanceof Request ? input.url : input);
  const json = (body) =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  if (url.includes('/api/session')) return json({ token: 'fixture-token', remote: false });
  if (url.includes('/api/apps')) return json({ apps });
  if (url.includes('/api/engine'))
    return json({ status: 'running', endpoint: 'fixture', apps_installed: apps.length });
  if (url.includes('/api/platforms')) return json({ current: 'windows', supported: ['windows'] });
  if (url.startsWith('/api/')) return json({});
  return real(input, init);
};

const LOREM = Array.from({ length: 40 }, (unused, index) => `Message ${index + 1}`);

// The conversation pattern: optional history, a scrolling transcript and a
// composer that grows with the draft.
function Conversation() {
  const [input, setInput] = useState('');
  return (
    <WorkspacePage scroll={false} compactSearch nav={false} className="ask-main" title="Ask">
      <div className="ask-workspace">
        <div className="chat-stage">
          <div className="chat-log" data-testid="transcript" tabIndex={0}>
            <div className="chat-transcript">
              {LOREM.map((line) => (
                <div className="chat-turn" key={line}>
                  <div className="chat-assistant">
                    <div className="chat-text">
                      <p>{line}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
        <ChatComposer input={input} setInput={setInput} busy={false} onSend={() => {}} />
      </div>
    </WorkspacePage>
  );
}

// The document/form pattern, plus the inspector/overlay pattern in both of its
// existing shapes: a modal dialog and a drawer.
function Forms() {
  const [dialog, setDialog] = useState(false);
  const [drawer, setDrawer] = useState(false);
  return (
    <WorkspacePage title="Settings">
      <div className="page-inner">
        <div className="panel">
          <div className="form-grid">
            <div className="field">
              <FormField label="Server name">
                <input defaultValue="vela.local" data-testid="text-field" />
              </FormField>
            </div>
            <div className="field">
              <FormField label="Notes">
                <textarea defaultValue="A draft" data-testid="textarea-field" />
              </FormField>
            </div>
            <div className="field">
              <FormField label="Theme">
                <select data-testid="select-field">
                  <option>Light</option>
                  <option>Dark</option>
                </select>
              </FormField>
            </div>
          </div>
          <button className="btn" onClick={() => setDialog(true)}>
            Open form dialog
          </button>
          <button className="btn" onClick={() => setDrawer(true)}>
            Open drawer
          </button>
        </div>
        <p className="page-sub" data-testid="long-word">
          supercalifragilisticexpialidocious-and-a-hostname-that-never-wraps.example.internal
        </p>
        {LOREM.map((line) => (
          <p key={line}>{line}</p>
        ))}
      </div>
      <Dialog open={dialog} onClose={() => setDialog(false)} aria-label="Form dialog">
        <h2>Add a source</h2>
        <div className="field">
          <FormField label="Address">
            <input defaultValue="https://example.internal" data-testid="dialog-field" />
          </FormField>
        </div>
        {LOREM.map((line) => (
          <p key={line}>{line}</p>
        ))}
        <div className="dialog-actions">
          <button className="btn" onClick={() => setDialog(false)}>
            Cancel
          </button>
        </div>
      </Dialog>
      {drawer && (
        <Drawer open onClose={() => setDrawer(false)} aria-label="Details drawer">
          <div className="drawer-header">
            <h2 className="drawer-name">Details</h2>
          </div>
          <div className="drawer-body" data-testid="drawer-body">
            {LOREM.map((line) => (
              <p key={line}>{line}</p>
            ))}
          </div>
          <div className="drawer-footer">
            <div className="actions">
              <button className="btn" onClick={() => setDrawer(false)}>
                Close
              </button>
            </div>
          </div>
        </Drawer>
      )}
    </WorkspacePage>
  );
}

function Page({ label }) {
  return (
    <WorkspacePage title={label}>
      <div className="page-inner">
        <h1 className="page-title">{label}</h1>
        <div className="tiles-grid">
          {apps.map((app) => (
            <button className="tile-card" key={app.id}>
              <span className="tile-card-text">{app.name}</span>
            </button>
          ))}
        </div>
      </div>
    </WorkspacePage>
  );
}

const router = createMemoryRouter(
  createRoutesFromElements(
    <Route
      element={
        <EngineProvider>
          <AppsProvider>
            <SettingsProvider>
              <Outlet />
            </SettingsProvider>
          </AppsProvider>
        </EngineProvider>
      }
    >
      <Route element={<Shell />}>
        {dashboardPages.map(({ to, label }) => (
          <Route key={to} path={to} element={<Page label={label} />} />
        ))}
        <Route path="/conversation" element={<Conversation />} />
        <Route path="/forms" element={<Forms />} />
      </Route>
    </Route>,
  ),
  { initialEntries: [new URLSearchParams(location.search).get('at') || '/'] },
);

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
