import {
  ChatCircleText,
  GearSix,
  HardDrives,
  HouseSimple,
  Lightning,
  SquaresFour,
  Storefront,
} from '@phosphor-icons/react';
import Home from './pages/Home.jsx';
import Ask from './pages/Ask.jsx';
import Apps from './pages/Apps.jsx';
import Library from './pages/Library.jsx';
import Environments from './pages/Environments.jsx';
import Automations from './pages/Automations.jsx';

// These pages share the dashboard shell. Embedded app routes stay in main.jsx.
// `rail` places a destination in the narrow rail: `primary` sits above the
// installed-app shortcuts, `tools` below the separator. Phones show the same
// rail inside a drawer, so every destination is reachable at every width.
export const dashboardPages = [
  { to: '/', label: 'Home', end: true, icon: HouseSimple, rail: 'primary', component: Home },
  {
    to: '/ask',
    label: 'Ask',
    icon: ChatCircleText,
    rail: 'primary',
    // A selected conversation is part of the route, so reloads and links restore it.
    childPaths: ['/ask/:conversationId'],
    component: Ask,
  },
  { to: '/apps', label: 'Apps', icon: SquaresFour, rail: 'tools', railOrder: 3, component: Apps },
  {
    to: '/library',
    label: 'Library',
    icon: Storefront,
    rail: 'tools',
    railOrder: 1,
    component: Library,
  },
  {
    to: '/environments',
    label: 'System',
    icon: HardDrives,
    rail: 'tools',
    railOrder: 4,
    component: Environments,
  },
  {
    to: '/automations',
    label: 'Automations',
    icon: Lightning,
    weight: 'fill',
    rail: 'tools',
    railOrder: 2,
    // The editor is part of the route so a reload, a link and the browser's
    // back button all land on the same automation.
    childPaths: ['/automations/:workflowId'],
    component: Automations,
  },
  { to: '/settings', label: 'Settings', icon: GearSix, rail: 'foot', component: Home, popup: true },
];

export const railGroup = (group) =>
  dashboardPages
    .filter((page) => page.rail === group)
    .sort((a, b) => (a.railOrder ?? 0) - (b.railOrder ?? 0));
