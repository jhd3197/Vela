import Button from '../components/ui/Button.jsx';
import { useMemo, useState } from 'react';
import {
  ArrowRight,
  ArrowsMerge,
  Basket,
  Bell,
  CalendarDots,
  Camera,
  CheckCircle,
  CircleNotch,
  Clock,
  DotsThreeVertical,
  DownloadSimple,
  FileArrowDown,
  HardDrives,
  NotePencil,
  Plus,
  Receipt,
  Scales,
  WarningCircle,
} from '@phosphor-icons/react';
import AppIcon from '../components/AppIcon.jsx';
import WorkspacePage from '../components/WorkspacePage.jsx';

// Automations page per the prototype. There is no automations engine in the
// backend yet, so this is a design preview: the rules below are built-in
// examples and the toggles only change local UI state. Marked "Preview".
const AUTOMATIONS = [
  {
    id: 'health-import',
    name: 'Apple Health import',
    app: { id: 'health', color: '#2bb6d8' },
    sub: 'Runs when a file lands in /imports',
    running: true,
    steps: [
      { icon: FileArrowDown, label: 'File dropped in /imports' },
      { icon: ArrowsMerge, label: 'Parse & merge into Health' },
      { icon: Bell, label: 'Notify when done' },
    ],
  },
  {
    id: 'grocery-list',
    name: 'Grocery list from the meal plan',
    app: { id: 'meals', color: '#9184d9' },
    sub: 'Every Sunday 18:00',
    steps: [
      { icon: Clock, label: 'Sunday 18:00' },
      { icon: Basket, label: "Build list from week's recipes" },
      { icon: NotePencil, label: 'Save to Notes' },
    ],
  },
  {
    id: 'snapshot-on-update',
    name: 'Snapshot before every app update',
    app: { id: 'vela-core' },
    sub: 'Built into Vela Core',
    steps: [
      { icon: DownloadSimple, label: 'Any app update' },
      { icon: HardDrives, label: 'Snapshot app storage' },
    ],
  },
  {
    id: 'weigh-in',
    name: 'Monday weigh-in reminder',
    app: { id: 'health', color: '#2bb6d8', glyph: Scales },
    sub: 'Every Monday 07:00',
    steps: [
      { icon: Clock, label: 'Monday 07:00' },
      { icon: Bell, label: 'Notify on this device only' },
    ],
  },
  {
    id: 'budget-rollup',
    name: 'Monthly budget rollup',
    app: { id: 'finance', color: '#21a377' },
    sub: '1st of the month 00:05',
    paused: true,
    steps: [
      { icon: CalendarDots, label: '1st of the month 00:05' },
      { icon: NotePencil, label: 'Write a summary note' },
    ],
  },
];

const BLUEPRINTS = [
  { icon: Camera, label: 'Import phone photos nightly' },
  { icon: Receipt, label: 'File receipts into Money' },
  { icon: WarningCircle, label: 'Warn under 20 GB free' },
];

const ACTIVITY = [
  { icon: CircleNotch, color: 'var(--accent)', title: 'Apple Health import', sub: 'Running now' },
  {
    icon: CheckCircle,
    color: 'var(--green)',
    title: 'Snapshot before update',
    sub: 'Sample run · 03:00',
  },
  {
    icon: CheckCircle,
    color: 'var(--green)',
    title: 'Grocery list built',
    sub: 'Sample run · yesterday',
  },
];

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'active', label: 'Active' },
  { key: 'paused', label: 'Paused' },
];

export default function Automations() {
  const [filter, setFilter] = useState('all');
  const [enabled, setEnabled] = useState(
    () => new Set(AUTOMATIONS.filter((a) => !a.paused).map((a) => a.id)),
  );

  const toggle = (id) => {
    setEnabled((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const visible = useMemo(() => {
    if (filter === 'active') return AUTOMATIONS.filter((a) => enabled.has(a.id));
    if (filter === 'paused') return AUTOMATIONS.filter((a) => !enabled.has(a.id));
    return AUTOMATIONS;
  }, [filter, enabled]);

  return (
    <WorkspacePage>
      <div className="page-inner">
        <header className="page-head-row">
          <div>
            <h1 className="page-title">
              Automations{' '}
              <span className="tag tag-accent" style={{ verticalAlign: 'middle' }}>
                Preview
              </span>
            </h1>
            <p className="page-sub">
              Rules wired between your apps, running on your machine. The engine isn't built yet —
              these are example rules to show the design.
            </p>
          </div>
          <Button variant="primary" disabled title="Coming with the automations engine">
            <Plus size={15} />
            New automation
          </Button>
        </header>

        <div className="auto-columns">
          <div className="auto-main">
            <div className="auto-summary">
              <span className="auto-summary-text">
                <span className="auto-summary-title">
                  {enabled.size} of {AUTOMATIONS.length} rules active
                </span>
                <span className="auto-summary-sub">Everything runs locally on this machine</span>
              </span>
              <span className="sparkline" aria-hidden="true">
                {[40, 65, 30, 80, 55, 100].map((h, i) => (
                  <span
                    key={i}
                    className={`sparkline-bar${i === 5 ? ' sparkline-bar-hot' : ''}`}
                    style={{ height: `${h}%` }}
                  />
                ))}
              </span>
            </div>

            <div className="seg" role="tablist" style={{ alignSelf: 'flex-start' }}>
              {FILTERS.map((f) => (
                <button
                  key={f.key}
                  role="tab"
                  aria-selected={filter === f.key}
                  className={`seg-opt${filter === f.key ? ' seg-opt-active' : ''}`}
                  onClick={() => setFilter(f.key)}
                >
                  {f.label}
                </button>
              ))}
            </div>

            <div className="auto-list">
              {visible.length === 0 && (
                <div className="state-block">
                  <h2>Nothing here</h2>
                  <p>No rules match this filter right now.</p>
                </div>
              )}
              {visible.map((rule) => {
                const on = enabled.has(rule.id);
                return (
                  <article key={rule.id} className={`auto-card${on ? '' : ' auto-card-paused'}`}>
                    <div className="auto-head">
                      <AppIcon app={rule.app} size={34} />
                      <span className="auto-head-text">
                        <span className="auto-title">{rule.name}</span>
                        <span
                          className={`auto-sub${rule.running && on ? ' auto-sub-running' : ''}`}
                        >
                          {on ? (rule.running ? 'Running now' : rule.sub) : 'Paused'}
                        </span>
                      </span>
                      <span className="auto-side">
                        <button
                          className={`switch${on ? ' switch-on' : ''}`}
                          role="switch"
                          aria-checked={on}
                          aria-label={`${on ? 'Pause' : 'Activate'} ${rule.name}`}
                          onClick={() => toggle(rule.id)}
                        />
                        <DotsThreeVertical size={18} style={{ color: 'var(--text-faint)' }} />
                      </span>
                    </div>
                    <div className="auto-steps">
                      {rule.steps.map((step, i) => (
                        <span key={step.label} style={{ display: 'contents' }}>
                          {i > 0 && <ArrowRight size={14} className="step-arrow" />}
                          <span className="step-chip">
                            <step.icon size={14} />
                            {step.label}
                          </span>
                        </span>
                      ))}
                      {rule.app.id === 'vela-core' && <span className="tag">Vela Core</span>}
                    </div>
                    {rule.running && on && <span className="auto-pulse" aria-hidden="true" />}
                  </article>
                );
              })}
            </div>

            <section className="blueprints">
              <div>
                <h2 className="blueprints-title">Start from a blueprint</h2>
                <p className="blueprints-sub">
                  Any app that declares <code className="mono">"permissions": ["notify"]</code> can
                  be wired into a rule.
                </p>
              </div>
              <div className="blueprints-actions">
                {BLUEPRINTS.map((b) => (
                  <Button key={b.label} disabled title="Coming with the automations engine">
                    <b.icon size={15} />
                    {b.label}
                  </Button>
                ))}
              </div>
            </section>
          </div>

          <aside className="activity-rail">
            <h2 className="section-head">Today</h2>
            <div className="activity-list">
              {ACTIVITY.map((item) => (
                <span key={item.title} className="activity-item">
                  <item.icon size={14} weight="fill" style={{ color: item.color }} />
                  <span className="activity-item-text">
                    <span>{item.title}</span>
                    <span className="activity-item-sub">{item.sub}</span>
                  </span>
                </span>
              ))}
            </div>
            <span className="activity-divider" />
            <div className="activity-stats">
              <span className="activity-stat">
                <span>Runs this week</span>
                <span>—</span>
              </span>
              <span className="activity-stat">
                <span>Failures</span>
                <span>—</span>
              </span>
              <span className="activity-stat">
                <span>Average run</span>
                <span>—</span>
              </span>
            </div>
            <Button block disabled title="Coming with the automations engine">
              Open run log
            </Button>
          </aside>
        </div>
      </div>
    </WorkspacePage>
  );
}
