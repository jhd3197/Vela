import { dashboardPages } from './navigation.js';
import React from 'react';
import ReactDOM from 'react-dom/client';
import {
  createBrowserRouter,
  createRoutesFromElements,
  RouterProvider,
  Route,
  Outlet,
} from 'react-router-dom';
import { AppsProvider } from './store.jsx';
import { EngineProvider } from './engine.jsx';
import { registerServiceWorker } from './pwa.js';
import Shell from './components/Shell.jsx';
import ThemeSync from './components/ThemeSync.jsx';
import Home from './pages/Home.jsx';
import AppView from './pages/AppView.jsx';
import AuthGate from './components/AuthGate.jsx';
import { initTheme } from './theme.js';
import './styles/main.scss';

registerServiceWorker();
initTheme();

const router = createBrowserRouter(
  createRoutesFromElements(
    <Route
      element={
        <AuthGate>
          <EngineProvider>
            <AppsProvider>
              <ThemeSync />
              <Outlet />
            </AppsProvider>
          </EngineProvider>
        </AuthGate>
      }
    >
      <Route element={<Shell />}>
        {dashboardPages.map(({ to, component: Page }) => (
          <Route key={to} path={to} element={<Page />} />
        ))}
      </Route>
      {/* The manifest chooses the app view's host navigation. */}
      <Route path="/app/:id" element={<AppView />} />
      <Route path="*" element={<Home />} />
    </Route>,
  ),
);

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
