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
import Launchpad from './pages/Launchpad.jsx';
import Library from './pages/Library.jsx';
import System from './pages/System.jsx';
import Automations from './pages/Automations.jsx';

// These pages share the dashboard shell. Embedded app routes stay in main.jsx.
//
// `rail: 'primary'` fixes a destination at the top of the rail (Desk and the
// Launchpad); `rail: 'foot'` sits at the bottom (Settings). Everything else the
// user reaches is either an installed app or a `core` app — Vela's own tools,
// which appear in the Launchpad's "Vela" section, rank in search like apps, and
// can be pinned to the rail like any app. `developer` marks a destination that
// only appears while "Show developer tools" is on; its route stays valid
// either way. `color` tints its icon tile in the Nocturne accent family.
export const dashboardPages = [
  { to: '/', label: 'Desk', end: true, icon: HouseSimple, rail: 'primary', component: Desk },
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
    to: '/ask',
    label: 'Ask',
    icon: ChatCircleText,
    core: true,
    id: 'ask',
    color: '#796cbf',
    // A selected conversation is part of the route, so reloads and links restore it.
    childPaths: ['/ask/:conversationId'],
    component: Ask,
  },
  {
    to: '/library',
    label: 'Marketplace',
    icon: Storefront,
    core: true,
    id: 'library',
    color: '#2bb6d8',
    component: Library,
  },
  {
    to: '/automations',
    label: 'Automations',
    icon: Lightning,
    weight: 'fill',
    core: true,
    id: 'automations',
    color: '#d08a2e',
    // The editor is part of the route so a reload, a link and the browser's
    // back button all land on the same automation.
    childPaths: ['/automations/:workflowId'],
    component: Automations,
  },
  {
    to: '/environments',
    label: 'System',
    icon: HardDrives,
    core: true,
    id: 'system',
    color: '#21a377',
    developer: true,
    component: System,
  },
  {
    to: '/settings',
    label: 'Settings',
    icon: GearSix,
    rail: 'foot',
    core: true,
    id: 'settings',
    color: '#75798c',
    component: Desk,
    popup: true,
  },
];

export const routablePages = dashboardPages.filter((page) => page.to && page.component);

export const visiblePages = (developer) =>
  dashboardPages.filter((page) => !page.developer || developer);

export const railGroup = (group, developer = false) =>
  visiblePages(developer)
    .filter((page) => page.rail === group)
    .sort((a, b) => (a.railOrder ?? 0) - (b.railOrder ?? 0));

// Vela's own tools as core apps: the Launchpad draws them in its "Vela"
// section, the rail can pin them, and search ranks them with installed apps.
// One source of truth — the `core` pages above — so an id, colour or label is
// defined once. System is developer-only.
export const coreApps = (developer = false) =>
  visiblePages(developer)
    .filter((page) => page.core)
    .map((page) => ({
      id: page.id,
      label: page.label,
      to: page.to,
      icon: page.icon,
      color: page.color,
      popup: page.popup,
      developer: page.developer,
    }));

export const isCoreId = (id) => dashboardPages.some((page) => page.core && page.id === id);

export const coreById = (id) => coreApps(true).find((entry) => entry.id === id) || null;
