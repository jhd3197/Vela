import { routablePages } from './navigation.js';
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
import SecurityProvider from './components/SecurityProvider.jsx';
import DesktopsProvider from './desktops/DesktopsProvider.jsx';
import AppsOverlayProvider from './desktops/AppsOverlay.jsx';
import Desk from './pages/Desk.jsx';
import DesktopRoute from './desktops/DesktopRoute.jsx';
import PhoneSetup from './pages/PhoneSetup.jsx';
import AppView from './pages/AppView.jsx';
import AuthGate from './components/AuthGate.jsx';
import ConfirmProvider from './components/ConfirmProvider.jsx';
import { installErrorReporting } from './errors.js';
import ErrorBoundary from './components/ErrorBoundary.jsx';
import { initTheme } from './theme.js';
import { sharedViewport } from './viewport.js';
import './styles/main.scss';

registerServiceWorker();
initTheme();
// Record the dashboard's own failures so they can be read back later.
installErrorReporting();
// One viewport owner for the whole dashboard; layout reads its CSS variables.
sharedViewport();

const router = createBrowserRouter(
  createRoutesFromElements(
    <>
      <Route path="/setup" element={<PhoneSetup />} />
      <Route
        element={
          // Outside the auth gate, not inside it: signing out asks "are you
          // sure?" too, and there is one dialog for the whole dashboard.
          <ConfirmProvider>
            <AuthGate>
              <SecurityProvider>
                <EngineProvider>
                  <AppsProvider>
                    <ThemeSync />
                    {/* Above the routes: the rail, the desk and the Launchpad
                        all need to agree about which desktop is being looked
                        at. */}
                    <DesktopsProvider>
                      <SettingsProvider>
                        {/* All apps draws over the page, so its host wraps the
                            routes rather than being one of them. */}
                        <AppsOverlayProvider>
                          <Outlet />
                        </AppsOverlayProvider>
                      </SettingsProvider>
                    </DesktopsProvider>
                  </AppsProvider>
                </EngineProvider>
              </SecurityProvider>
            </AuthGate>
          </ConfirmProvider>
        }
      >
        <Route element={<Shell />}>
          {routablePages.flatMap(({ to, childPaths = [], component: Page }) =>
            [to, ...childPaths].map((path) => (
              // Around the page, not the shell: a page that throws must not
              // take the rail with it.
              <Route
                key={path}
                path={path}
                element={
                  <ErrorBoundary>
                    <Page />
                  </ErrorBoundary>
                }
              />
            )),
          )}
        </Route>
        {/* A link straight to one workspace. It selects that desktop for this
            browser and shows the ordinary desk. */}
        <Route
          path="/desktops/:desktopId"
          element={
            <Shell>
              <ErrorBoundary>
                <DesktopRoute />
              </ErrorBoundary>
            </Shell>
          }
        />
        {/* The manifest chooses the app view's host navigation. */}
        <Route path="/app/:id" element={<AppView />} />
        <Route path="*" element={<Desk />} />
      </Route>
    </>,
  ),
);

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
