import React from 'react';
import { createRoot } from 'react-dom/client';
import {
  createMemoryRouter,
  Outlet,
  RouterProvider,
  Route,
  createRoutesFromElements,
} from 'react-router-dom';
import { routablePages } from '../../src/navigation.js';
import { AppsProvider } from '../../src/store.jsx';
import { EngineProvider } from '../../src/engine.jsx';
import SettingsProvider from '../../src/components/SettingsProvider.jsx';
import Shell from '../../src/components/Shell.jsx';
import AppView from '../../src/pages/AppView.jsx';
import WorkspacePage from '../../src/components/WorkspacePage.jsx';
import '../../src/styles/main.scss';

// Browser-only fixture: disposable app records served from a stubbed fetch.
// It never reaches a Vela server or a user's installed apps.
const SETS = {
  empty: [],
  many: [
    { id: 'alpha', name: 'Alpha', installed: true, running: true, color: '#7c6bd4' },
    { id: 'beta', name: 'Beta', installed: true, running: false, color: '#f0a93c' },
    {
      id: 'long',
      name: 'A remarkably long application name that should never widen the rail',
      installed: true,
      running: false,
    },
    { id: 'dup-a', name: 'Duplicate', installed: true, running: true, color: '#2bb6d8' },
    { id: 'dup-b', name: 'Duplicate', installed: true, running: false, color: '#21a377' },
    { id: 'noicon', name: 'No colour', installed: true, running: false },
    { id: 'gamma', name: 'Gamma', installed: true, running: true, color: '#9184d9' },
    { id: 'delta', name: 'Delta', installed: true, running: false, color: '#d5483a' },
    { id: 'epsilon', name: 'Epsilon', installed: true, running: false, color: '#5be3b4' },
    { id: 'zeta', name: 'Zeta', installed: true, running: true, color: '#c4a7ff' },
    { id: 'shop', name: 'Uninstalled', installed: false, supported: true, category: 'wellness' },
    // Two presentation contracts side by side: an app that asked for the hub's
    // own chrome keeps the rail at every width, and one that did not is
    // unaffected by that choice.
    {
      id: 'workspace',
      name: 'Workspace app',
      installed: true,
      running: true,
      color: '#7c4dee',
      view: { surface: 'none', chrome: 'hub' },
    },
    {
      id: 'standalone',
      name: 'Standalone app',
      installed: true,
      running: true,
      color: '#21a377',
      view: { surface: 'none', chrome: 'compact' },
    },
  ],
};

// The same installation with nothing running, so the OPEN group can be checked
// for absence as well as presence.
SETS.idle = SETS.many.map((app) => ({ ...app, running: false }));

const set = new URLSearchParams(location.search).get('apps') || 'many';
const apps = (SETS[set] || SETS.many).map((app) => ({
  version: '1.0.0',
  category: 'utilities',
  supported: true,
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
    return json({
      status: 'running',
      endpoint: 'fixture',
      apps_installed: apps.filter((a) => a.installed).length,
      apps_running: apps.filter((a) => a.running).length,
      storage_bytes: 1024,
    });
  if (url.includes('/api/platforms')) return json({ current: 'windows', supported: ['windows'] });
  // The default pins: Ask and the Library. PATCH is accepted so the pin/unpin
  // controls persist optimistically without a real server.
  if (url.includes('/api/settings')) return json({ rail: { pinned: ['ask', 'library'] } });
  if (url.includes('/api/notifications')) return json({ notifications: [] });
  if (url.startsWith('/api/')) return json({});
  return real(input, init);
};

function Page({ label }) {
  return (
    <WorkspacePage>
      <div className="page-inner">
        <h1 className="page-title">{label}</h1>
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
        {routablePages.map(({ to, label }) => (
          <Route key={to} path={to} element={<Page label={label} />} />
        ))}
      </Route>
      {/* The manifest chooses the host navigation, exactly as in the real app. */}
      <Route path="/app/:id" element={<AppView />} />
    </Route>,
  ),
  { initialEntries: ['/'] },
);

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
