import React from 'react';
import ReactDOM from 'react-dom/client';
import { createBrowserRouter, createRoutesFromElements, RouterProvider, Route, Outlet } from 'react-router-dom';
import { AppsProvider } from './store.jsx';
import { registerServiceWorker } from './pwa.js';
import Shell from './components/Shell.jsx';
import ThemeSync from './components/ThemeSync.jsx';
import Home from './pages/Home.jsx';
import Apps from './pages/Apps.jsx';
import Library from './pages/Library.jsx';
import Environments from './pages/Environments.jsx';
import Automations from './pages/Automations.jsx';
import Ask from './pages/Ask.jsx';
import Settings from './pages/Settings.jsx';
import AppView from './pages/AppView.jsx';
import AuthGate from './components/AuthGate.jsx';
import { initTheme } from './theme.js';
import './index.css';

registerServiceWorker();
initTheme();

const router = createBrowserRouter(createRoutesFromElements(
  <Route element={<AuthGate><AppsProvider><ThemeSync /><Outlet /></AppsProvider></AuthGate>}>
    <Route element={<Shell />}>
      <Route path="/" element={<Home />} />
      <Route path="/apps" element={<Apps />} />
      <Route path="/library" element={<Library />} />
      <Route path="/environments" element={<Environments />} />
      <Route path="/automations" element={<Automations />} />
      <Route path="/ask" element={<Ask />} />
      <Route path="/settings" element={<Settings />} />
    </Route>
    {/* The manifest chooses the app view's host navigation. */}
    <Route path="/app/:id" element={<AppView />} />
    <Route path="*" element={<Home />} />
  </Route>,
));

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
