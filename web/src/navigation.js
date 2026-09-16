import {
  ChatCircleText,
  GearSix,
  HardDrives,
  HouseSimple,
  Lightning,
  SquaresFour,
  Storefront,
} from '@phosphor-icons/react';
import Desk from './pages/Desk.jsx';
import Ask from './pages/Ask.jsx';
import Apps from './pages/Apps.jsx';
import Launchpad from './pages/Launchpad.jsx';
import Library from './pages/Library.jsx';
import Environments from './pages/Environments.jsx';
import Automations from './pages/Automations.jsx';

// These pages share the dashboard shell. Embedded app routes stay in main.jsx.
// `rail` places a destination: `primary` sits above the installed-app
// shortcuts, `tools` below the separator, and `more` inside the labelled
// secondary menu. `developer` marks a destination that only appears while
// "Show developer tools" is on; its route stays valid either way.
export const dashboardPages = [
  { to: '/', label: 'Desk', end: true, icon: HouseSimple, rail: 'primary', component: Desk },
  {
    to: '/ask',
    label: 'Ask',
    icon: ChatCircleText,
    rail: 'primary',
    // A selected conversation is part of the route, so reloads and links restore it.
    childPaths: ['/ask/:conversationId'],
    component: Ask,
  },
  // The Launchpad: a full-screen grid of every app over the blurred wallpaper.
  // It replaces the All apps drawer and the old Manage apps page as the one
  // place that answers "which apps do I have".
  {
    to: '/apps',
    label: 'Launchpad',
    icon: SquaresFour,
    rail: 'primary',
    railOrder: 2,
    end: true,
    component: Launchpad,
  },
  {
    to: '/library',
    label: 'Library',
    icon: Storefront,
    rail: 'tools',
    railOrder: 1,
    component: Library,
  },
  {
    to: '/automations',
    label: 'Automations',
    icon: Lightning,
    weight: 'fill',
    rail: 'more',
    railOrder: 1,
    // The editor is part of the route so a reload, a link and the browser's
    // back button all land on the same automation.
    childPaths: ['/automations/:workflowId'],
    component: Automations,
  },
  // Manage apps is retired as a destination: Stage 3 folds it into the
  // Marketplace. Until then the page stays reachable at its own path so the
  // old flows keep working and `/apps/manage` has somewhere to land.
  {
    to: '/apps/manage',
    label: 'Manage apps',
    icon: SquaresFour,
    component: Apps,
  },
  {
    to: '/environments',
    label: 'System',
    icon: HardDrives,
    rail: 'more',
    railOrder: 3,
    developer: true,
    component: Environments,
  },
  { to: '/settings', label: 'Settings', icon: GearSix, rail: 'foot', component: Desk, popup: true },
];

// A rail entry that opens a drawer is not a destination: it has no path and no
// component, so it never becomes a route.
export const routablePages = dashboardPages.filter((page) => page.to && page.component);

export const visiblePages = (developer) =>
  dashboardPages.filter((page) => !page.developer || developer);

export const railGroup = (group, developer = false) =>
  visiblePages(developer)
    .filter((page) => page.rail === group)
    .sort((a, b) => (a.railOrder ?? 0) - (b.railOrder ?? 0));

// Vela's own tools, presented as apps in the Launchpad's "Vela" section. Stage
// 2 of the OS-shell plan gives these real ids, colours and artwork and lets
// them be pinned to the rail; for now they carry the fields the Launchpad
// needs to draw and open them. System is a developer-only tool.
const CORE_APPS = [
  { id: 'ask', label: 'Ask', to: '/ask', icon: ChatCircleText },
  { id: 'automations', label: 'Automations', to: '/automations', icon: Lightning },
  { id: 'library', label: 'Library', to: '/library', icon: Storefront },
  { id: 'settings', label: 'Settings', to: '/settings', icon: GearSix, popup: true },
  { id: 'system', label: 'System', to: '/environments', icon: HardDrives, developer: true },
];

export const coreApps = (developer = false) =>
  CORE_APPS.filter((entry) => !entry.developer || developer);
