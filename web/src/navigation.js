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
  {
    to: '/apps',
    label: 'Manage apps',
    icon: SquaresFour,
    rail: 'more',
    railOrder: 2,
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

export const visiblePages = (developer) =>
  dashboardPages.filter((page) => !page.developer || developer);

export const railGroup = (group, developer = false) =>
  visiblePages(developer)
    .filter((page) => page.rail === group)
    .sort((a, b) => (a.railOrder ?? 0) - (b.railOrder ?? 0));
