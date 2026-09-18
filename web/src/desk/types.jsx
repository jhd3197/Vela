// The widget types Vela itself provides.
//
// A type ships only when its data source exists. Money, Meals, Photos,
// Paperless and weather appear in the mockups; none of them has a source on
// the host, so none of them is here. App-owned widgets arrive
// through the manifest instead — see `registry.js`.
import {
  Archive,
  ChatTeardropDots,
  Clock,
  HardDrives,
  Lightning,
  PlayCircle,
  SquaresFour,
  Stethoscope,
  WarningCircle,
} from '@phosphor-icons/react';
import AppsWidget from './widgets/AppsWidget.jsx';
import AskWidget from './widgets/AskWidget.jsx';
import HealthWidget from './widgets/HealthWidget.jsx';
import NeedsYouWidget from './widgets/NeedsYouWidget.jsx';
import RunningWidget from './widgets/RunningWidget.jsx';
import {
  BackupsWidget,
  FlowsWidget,
  SystemWidget,
  VolumeWidget,
} from './widgets/SystemWidgets.jsx';
import { WidgetClock } from './widgets/primitives.jsx';
import WidgetCard from './widgets/WidgetCard.jsx';

/**
 * The prototype's recipe, applied once.
 *
 * Every card on the Home mockup carries a tinted icon and a 14px heading, so
 * every widget here does too rather than ten widgets each remembering to. The
 * header is the widget's own content and not the frame's chrome: in view mode
 * `WidgetFrame` draws nothing at all, so this is what tells you which card is
 * which -- and it scrolls and wraps with the body, as the mockup's does.
 *
 * `bare` is for a widget that is its own heading. The clock is the only one:
 * the time is the content, and a row reading "Clock" above it would be saying
 * what the reader can already see.
 */
function withCard({ tone = 'accent', bare = false, render: Render }) {
  if (bare) return Render;
  return function CardedWidget(props) {
    return (
      <WidgetCard
        type={props.type}
        title={props.type?.title?.(props.cfg || {}) || undefined}
        tone={tone}
      >
        <Render {...props} />
      </WidgetCard>
    );
  };
}

const CORE_WIDGETS = [
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
    bare: true,
    render: ({ cfg, ctx }) => (
      <WidgetClock showSeconds={cfg.seconds === true} weather={ctx?.weather} />
    ),
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
    tone: 'accent',
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
    tone: 'cyan',
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
    tone: 'accent',
    render: AskWidget,
  },
  {
    id: 'needs-you',
    name: 'Needs you',
    icon: WarningCircle,
    cat: 'Vela',
    desc: 'Apps that said something needs your attention',
    w: 2,
    h: 1,
    min: [1, 1],
    defaultCfg: {},
    tone: 'amber',
    render: NeedsYouWidget,
  },
  {
    id: 'system',
    name: 'System',
    icon: HardDrives,
    cat: 'Vela',
    desc: 'This computer: uptime, CPU over the last few minutes, memory',
    w: 2,
    h: 1,
    min: [1, 1],
    defaultCfg: {},
    tone: 'neutral',
    render: SystemWidget,
  },
  {
    id: 'volume',
    name: 'Volume',
    icon: HardDrives,
    cat: 'Vela',
    desc: 'How full one of your volumes is',
    w: 2,
    h: 1,
    min: [1, 1],
    defaultCfg: { path: '' },
    options: 'volume',
    // The board stores a path, so the frame is named after the volume it is
    // pointed at rather than reading "Volume" three times on one desk.
    title: (cfg) => cfg.label || '',
    tone: 'cyan',
    render: VolumeWidget,
  },
  {
    id: 'flows',
    name: 'Flows',
    icon: Lightning,
    cat: 'Vela',
    desc: 'Automation runs today, failures and the average run',
    w: 2,
    h: 1,
    min: [1, 1],
    defaultCfg: {},
    tone: 'accent',
    render: FlowsWidget,
  },
  {
    id: 'health',
    name: 'Health',
    icon: Stethoscope,
    cat: 'Vela',
    desc: "Whether this server has what it needs, from Vela's own checks",
    w: 2,
    h: 1,
    min: [1, 1],
    defaultCfg: {},
    tone: 'green',
    render: HealthWidget,
  },
  {
    id: 'backups',
    name: 'Backups',
    icon: Archive,
    cat: 'Vela',
    desc: 'When Vela last backed itself up, and a way to do it now',
    w: 2,
    h: 1,
    min: [1, 1],
    defaultCfg: {},
    tone: 'cyan',
    render: BackupsWidget,
  },
];

/**
 * The types the desk actually renders: the list above with the recipe applied.
 * `withCard` is done here rather than inside each widget so a widget file stays
 * about its own data, and so a widget added later cannot forget the header.
 */
export const CORE_WIDGET_TYPES = CORE_WIDGETS.map((type) => ({
  ...type,
  render: withCard(type),
}));
