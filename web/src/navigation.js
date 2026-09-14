import {
  ChatCircleText, GearSix, HardDrives, HouseSimple, Lightning, SquaresFour, Storefront,
} from '@phosphor-icons/react';
import Home from './pages/Home.jsx';
import Ask from './pages/Ask.jsx';
import Apps from './pages/Apps.jsx';
import Library from './pages/Library.jsx';
import Environments from './pages/Environments.jsx';
import Automations from './pages/Automations.jsx';
import Settings from './pages/Settings.jsx';

// These pages share the dashboard shell. Embedded app routes stay in main.jsx.
// Order also controls navigation; tabHidden keeps the phone bar to five items.
export const dashboardPages = [
  { to: '/', label: 'Home', end: true, icon: HouseSimple, component: Home },
  { to: '/ask', label: 'Ask', icon: ChatCircleText, component: Ask },
  { to: '/apps', label: 'Apps', icon: SquaresFour, component: Apps },
  { to: '/library', label: 'Library', icon: Storefront, component: Library },
  { to: '/environments', label: 'System', icon: HardDrives, tabHidden: true, component: Environments },
  { to: '/automations', label: 'Automations', icon: Lightning, weight: 'fill', tabHidden: true, component: Automations },
  { to: '/settings', label: 'Settings', icon: GearSix, component: Settings },
];
