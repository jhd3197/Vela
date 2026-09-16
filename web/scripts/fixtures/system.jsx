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
import WorkspacePage from '../../src/components/WorkspacePage.jsx';
import '../../src/styles/main.scss';

// Browser-only fixture for System: disposable log files served from a stubbed
// fetch. It never reaches a Vela server and never reads a real log.
const apps = [
  {
    id: 'notes',
    name: 'Notes',
    installed: true,
    running: true,
    runtime: 'process',
    version: '1.0.0',
    supported: true,
    schemaVersion: 2,
  },
];

const serverLines = [
  ...Array.from(
    { length: 240 },
    (_, n) => `2026-09-16 09:${String(n % 60).padStart(2, '0')}:00 INFO vela.api: request ${n + 1}`,
  ),
  '2026-09-16 09:41:00 WARNING vela.backups: disk is nearly full',
  '2026-09-16 09:42:00 ERROR vela.apps: notes exited unexpectedly',
];

const files = {
  'server.log': serverLines,
  'server.log.1': ['2026-09-15 08:00:00 INFO vela.api: yesterday'],
  'audit.log': ['2026-09-16 09:00:00 INFO vela.audit: install actor=local app=notes'],
  'notes.log': ['notes starting', 'notes ready'],
};

const kinds = {
  'server.log': 'server',
  'server.log.1': 'server',
  'audit.log': 'audit',
  'notes.log': 'app',
};

// Every call the viewer makes, answered from the table above. `cleared` proves
// the confirm flow reached the engine rather than only closing its dialog.
const real = window.fetch.bind(window);
window.fetch = async (input, init) => {
  const url = String(input instanceof Request ? input.url : input);
  const method = (init?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
  const json = (body, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });

  if (url.includes('/api/session')) return json({ token: 'fixture-token', remote: false });

  const logMatch = url.match(/\/api\/logs\/([^/?]+)/);
  if (logMatch) {
    const name = decodeURIComponent(logMatch[1]);
    const lines = files[name];
    if (!lines) return json({ detail: 'unknown log' }, 404);
    if (url.includes('/download')) {
      window.__downloadedLog = name;
      return new Response(lines.join('\n'), {
        status: 200,
        headers: { 'Content-Type': 'text/plain' },
      });
    }
    if (method === 'DELETE') {
      // The client sends a Headers object, so read it as one.
      const confirm = new Headers(init?.headers).get('X-Vela-Confirm');
      if (confirm !== 'clear') return json({ detail: 'confirm' }, 428);
      window.__clearedLog = name;
      files[name] = [];
      return json({ name, cleared: true });
    }
    const query = new URL(url, 'http://fixture').searchParams;
    const count = Number(query.get('lines') || 200);
    const pattern = query.get('pattern') || '';
    if (pattern) {
      const hits = lines.filter((line) => line.toLowerCase().includes(pattern.toLowerCase()));
      return json({
        name,
        lines: hits.slice(0, count),
        total: hits.length,
        truncated: hits.length > count,
        pattern,
      });
    }
    return json({
      name,
      lines: lines.slice(-count),
      total: lines.length,
      truncated: lines.length > count,
    });
  }

  if (url.includes('/api/logs')) {
    return json({
      logs: Object.entries(files).map(([name, lines]) => ({
        name,
        kind: kinds[name],
        size: lines.join('\n').length,
        modified: '2026-09-16T09:42:00',
        base: name.replace(/\.\d+$/, ''),
        rotated: /\.\d+$/.test(name),
      })),
    });
  }

  if (url.includes('/api/apps')) return json({ apps });
  if (url.includes('/api/engine'))
    return json({
      status: 'running',
      engine: 'local',
      version: '0.1.10',
      endpoint: 'http://127.0.0.1:7700',
      apps_installed: 1,
      apps_running: 1,
      storage_bytes: 5_242_880,
      data_dir: 'C:\\fixture\\.vela',
    });
  if (url.includes('/api/platforms')) return json({ current: 'windows', supported: ['windows'] });
  if (url.includes('/api/settings')) return json({ desk: { wallpaper: 'lake', dim: true } });
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
            element={to === '/environments' ? <Component /> : <Page label={label} />}
          />
        ))}
      </Route>
    </Route>,
  ),
  { initialEntries: ['/environments'] },
);

// A memory router keeps its location out of the address bar, so the suite
// reads it here. The page still writes the tab and the open log into the
// query exactly as it does against a real browser router.
window.__location = router.state.location.pathname + router.state.location.search;
router.subscribe((state) => {
  window.__location = state.location.pathname + state.location.search;
});

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
