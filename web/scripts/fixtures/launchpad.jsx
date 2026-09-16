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

// Browser-only fixture: disposable app records and an in-memory desk served
// from a stubbed fetch. It never reaches a Vela server or a user's own apps.
const apps = [
  {
    id: 'alpha',
    name: 'Alpha',
    installed: true,
    running: true,
    runtime: 'process',
    color: '#7c6bd4',
  },
  { id: 'beta', name: 'Beta', installed: true, running: false, runtime: 'web', color: '#f0a93c' },
  {
    id: 'health',
    name: 'Health',
    installed: true,
    running: true,
    runtime: 'process',
    category: 'wellness',
    color: '#21a377',
  },
  {
    id: 'notes',
    name: 'Notes',
    installed: true,
    running: false,
    runtime: 'web',
    category: 'productivity',
    color: '#2bb6d8',
    widgets: [{ id: 'recent', name: 'Recent notes', size: 'm' }],
  },
  { id: 'zeta', name: 'Zeta', installed: true, running: false, runtime: 'web', color: '#c4a7ff' },
  // Not installed: it belongs in the Marketplace, so the Launchpad must not
  // show it among the apps the user has.
  { id: 'shop', name: 'Shoppe', installed: false, supported: true, category: 'lifestyle' },
].map((app) => ({ version: '1.0.0', supported: true, schemaVersion: 2, ...app }));

// A minimal two-board desk so "Add widget to desk" has somewhere to write.
let desk = {
  revision: 1,
  boards: {
    version: 1,
    desktop: { cols: 6, widgets: [{ i: 'w1', type: 'clock', x: 0, y: 0, w: 2, h: 1, cfg: {} }] },
    phone: { cols: 2, widgets: [{ i: 'w1', type: 'clock', x: 0, y: 0, w: 2, h: 1, cfg: {} }] },
  },
};

const real = window.fetch.bind(window);
window.fetch = async (input, init) => {
  const url = String(input instanceof Request ? input.url : input);
  const method = (init?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
  const json = (body) =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  if (url.includes('/api/session')) return json({ token: 'fixture-token', remote: false });
  if (url.includes('/api/apps')) return json({ apps });
  if (url.includes('/api/desk')) {
    if (method === 'PUT') {
      const sent = JSON.parse(init.body);
      desk = { revision: desk.revision + 1, boards: sent.boards };
      return json(desk);
    }
    return json(desk);
  }
  if (url.includes('/api/engine'))
    return json({
      status: 'running',
      endpoint: 'fixture',
      apps_installed: apps.filter((a) => a.installed).length,
      apps_running: apps.filter((a) => a.running).length,
      storage_bytes: 1024,
    });
  if (url.includes('/api/platforms')) return json({ current: 'windows', supported: ['windows'] });
  if (url.includes('/api/widgets'))
    return json({ widgets: [{ appId: 'health', summary: { attention: true } }] });
  if (url.includes('/api/settings')) return json({ desk: { wallpaper: 'choroni', dim: true } });
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
        {routablePages.map(({ to, label, component: Component }) => (
          <Route
            key={to}
            path={to}
            element={to === '/apps' ? <Component /> : <Page label={label} />}
          />
        ))}
      </Route>
      <Route path="/app/:id" element={<AppView />} />
    </Route>,
  ),
  // Start on the Desk so the Launchpad has a route to return to on Escape.
  { initialEntries: ['/', '/apps'], initialIndex: 1 },
);

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
