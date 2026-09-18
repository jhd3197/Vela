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
import OperationsProvider from '../../src/operations/OperationsProvider.jsx';
import ConfirmProvider from '../../src/components/ConfirmProvider.jsx';
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

// Background work, in the shapes the real endpoints return. The suite moves
// `runs[0].status` to prove that System, the bell and the rail are reading one
// list rather than three.
const fixture = (window.__operations = {
  runs: [
    {
      id: 'run-1',
      workflowId: 'wf-1',
      workflowName: 'Nightly greeting',
      revision: 2,
      status: 'waiting',
      trigger: 'manual',
      queuedAt: '2026-09-16T09:40:00+00:00',
      startedAt: '2026-09-16T09:40:01+00:00',
      finishedAt: null,
      error: null,
    },
  ],
  attention: { desktops: {} },
  updates: { current: '0.1.10', latest: null, available: false, checkedAt: null },
  updateJob: { state: 'idle', percent: 0, message: '', version: null, rollback: false },
  backups: [{ name: '20260916-030000', size: 40960, created_at: '2026-09-16T03:00:00' }],
  doctor: {
    ranAt: '2026-09-16T09:00:00',
    checks: [
      {
        key: 'data-dir',
        title: 'Room to work',
        status: 'ok',
        detail: '120 GB free.',
        repairable: false,
        ranAt: '2026-09-16T09:00:00',
      },
    ],
    update: { available: false, latest: null, current: '0.1.10' },
  },
});

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

let errorRows = [
  {
    id: 1,
    source: 'server',
    type: 'RuntimeError',
    message: 'notes exited while saving',
    traceback: [
      'Traceback (most recent call last):',
      '  File "vela/lifecycle.py", line 88',
      'RuntimeError: notes exited while saving',
    ].join('\n'),
    endpoint: '/api/apps/notes/launch',
    count: 4,
    firstSeen: '2026-09-16T08:00:00',
    lastSeen: '2026-09-16T09:40:00',
    resolved: false,
  },
  {
    id: 2,
    source: 'dashboard',
    type: 'TypeError',
    message: 'Cannot read properties of undefined',
    traceback: null,
    endpoint: '/desk',
    count: 1,
    firstSeen: '2026-09-16T09:30:00',
    lastSeen: '2026-09-16T09:30:00',
    resolved: false,
  },
];

let bundles = [];

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

  if (url.includes('/api/errors/stats')) {
    const open = errorRows.filter((row) => !row.resolved);
    return json({ unresolved: open.length, lastDay: open.length, total: errorRows.length });
  }
  const resolveMatch = url.match(/\/api\/errors\/(\d+)\/resolve/);
  if (resolveMatch) {
    const row = errorRows.find((entry) => entry.id === Number(resolveMatch[1]));
    if (row) row.resolved = JSON.parse(init.body).resolved;
    return json(row || {});
  }
  const deleteMatch = url.match(/\/api\/errors\/(\d+)$/);
  if (deleteMatch && method === 'DELETE') {
    errorRows = errorRows.filter((entry) => entry.id !== Number(deleteMatch[1]));
    return new Response(null, { status: 204 });
  }
  if (url.includes('/api/errors')) {
    const query = new URL(url, 'http://fixture').searchParams;
    const wanted = query.get('resolved');
    const search = (query.get('search') || '').toLowerCase();
    let rows = errorRows;
    if (wanted === 'true') rows = rows.filter((row) => row.resolved);
    if (wanted === 'false') rows = rows.filter((row) => !row.resolved);
    if (search) rows = rows.filter((row) => row.message.toLowerCase().includes(search));
    return json({ errors: rows, total: rows.length, page: 1, pageSize: 25 });
  }

  if (url.includes('/api/support-bundle')) {
    if (method === 'POST') {
      bundles.unshift({
        name: 'vela-support-20260916-094200.zip',
        size: 20480,
        created_at: '2026-09-16T09:42:00',
      });
      return json(bundles[0], 201);
    }
    return json({ bundles });
  }

  if (url.includes('/api/automations/runs')) return json({ runs: fixture.runs });
  if (url.includes('/api/desktops/attention')) return json(fixture.attention);
  if (url.includes('/api/updates/job')) return json(fixture.updateJob);
  if (url.includes('/api/updates')) return json(fixture.updates);
  if (url.includes('/api/backups')) return json({ backups: fixture.backups });
  if (url.includes('/api/doctor')) return json(fixture.doctor);
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
        <ConfirmProvider>
          <EngineProvider>
            <AppsProvider>
              <OperationsProvider>
                <SettingsProvider>
                  <Outlet />
                </SettingsProvider>
              </OperationsProvider>
            </AppsProvider>
          </EngineProvider>
        </ConfirmProvider>
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
