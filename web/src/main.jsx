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
import SettingsProvider from './components/SettingsProvider.jsx';
import Home from './pages/Home.jsx';
import PhoneSetup from './pages/PhoneSetup.jsx';
import AppView from './pages/AppView.jsx';
import AuthGate from './components/AuthGate.jsx';
import { initTheme } from './theme.js';
import { sharedViewport } from './viewport.js';
import './styles/main.scss';

registerServiceWorker();
initTheme();
// One viewport owner for the whole dashboard; layout reads its CSS variables.
sharedViewport();

const router = createBrowserRouter(
  createRoutesFromElements(
    <>
      <Route path="/setup" element={<PhoneSetup />} />
      <Route
        element={
          <AuthGate>
            <EngineProvider>
              <AppsProvider>
                <ThemeSync />
                <SettingsProvider>
                  <Outlet />
                </SettingsProvider>
              </AppsProvider>
            </EngineProvider>
          </AuthGate>
        }
      >
        <Route element={<Shell />}>
          {dashboardPages.flatMap(({ to, childPaths = [], component: Page }) =>
            [to, ...childPaths].map((path) => <Route key={path} path={path} element={<Page />} />),
          )}
        </Route>
        {/* The manifest chooses the app view's host navigation. */}
        <Route path="/app/:id" element={<AppView />} />
        <Route path="*" element={<Home />} />
      </Route>
    </>,
  ),
);

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
