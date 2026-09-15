// The widget types Vela itself provides.
//
// A type ships only when its data source exists. Health, Money, Meals,
// Photos, Paperless and weather appear in the mockups; none of them has a
// source on the host, so none of them is here. App-owned widgets arrive
// through the manifest instead — see `registry.js`.
import { ChatTeardropDots, Clock, PlayCircle, SquaresFour } from '@phosphor-icons/react';
import AppsWidget from './widgets/AppsWidget.jsx';
import AskWidget from './widgets/AskWidget.jsx';
import RunningWidget from './widgets/RunningWidget.jsx';
import { WidgetClock } from './widgets/primitives.jsx';

export const CORE_WIDGET_TYPES = [
  {
    id: 'clock',
    name: 'Clock',
    icon: Clock,
    cat: 'Vela',
    desc: 'The time, the weekday and the date',
    w: 2,
    h: 1,
    min: [1, 1],
    defaultCfg: {},
    render: ({ cfg }) => <WidgetClock showSeconds={cfg.seconds === true} />,
  },
  {
    id: 'apps',
    name: 'Your apps',
    icon: SquaresFour,
    cat: 'Vela',
    desc: 'Every installed app, and one way to add another',
    w: 2,
    h: 2,
    min: [2, 1],
    defaultCfg: {},
    render: AppsWidget,
  },
  {
    id: 'running',
    name: 'Running now',
    icon: PlayCircle,
    cat: 'Vela',
    desc: 'The apps the engine has running',
    w: 2,
    h: 1,
    min: [1, 1],
    defaultCfg: {},
    render: RunningWidget,
  },
  {
    id: 'ask',
    name: 'Ask',
    icon: ChatTeardropDots,
    cat: 'Vela',
    desc: 'Your latest conversation, and a box to start the next one',
    w: 2,
    h: 2,
    min: [2, 1],
    defaultCfg: {},
    render: AskWidget,
  },
];
