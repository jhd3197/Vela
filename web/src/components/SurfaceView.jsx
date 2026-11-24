// Draws a surface-v1 document (vela-contracts/surface-v1.schema.json) with
// Vela's own components.
//
// The contract is data, never markup: text renders as text, images are only
// the inline data: URLs the schema allows, and a button can only name an
// action the document declared. Nothing here fetches anything — the window
// hands down a document and, when actions are wired, an
// `onAction(actionId, input)`. Pure: given the same document it draws the
// same thing.
//
// Ported from serverkit-gui `frontend/components/SurfaceView.jsx` (MIT, same
// owner). Its serverkit-sdk formatting became `surface-format.js`; its skins
// became Vela theme variables; and the desk's shared pieces (`components/ds/`)
// draw the nodes the contract says should look like the desk's widgets.
import { useEffect, useState } from 'react';
import { Square } from '@phosphor-icons/react';
import { Bars, Card, Meter, Sparkline, Stat, Tag } from './ds/index.js';
import { formatSurfaceValue } from './surface-format.js';

// The contract's tones onto the design system's. Nothing maps to accent: an
// untoned node gets it by default, which is the design system's "nothing
// particular to say".
const TONES = { neutral: 'neutral', good: 'green', warn: 'amber', bad: 'red', info: 'cyan' };
const toneOf = (value) => TONES[value];

export default function SurfaceView({ surface, onAction }) {
  if (!surface || surface.surface !== 1) {
    // A version this host does not know is shown as such rather than guessed
    // at — the contract refuses the whole document, never the part that parsed.
    return (
      <div className="vela-surface-refusal" role="status">
        This view uses a format this version cannot draw.
      </div>
    );
  }
  const actions = Object.fromEntries((surface.actions || []).map((action) => [action.id, action]));
  return (
    <div
      className="vela-surface"
      lang={typeof surface.lang === 'string' ? surface.lang : undefined}
    >
      <SurfaceNode node={surface.root} ctx={{ actions, onAction }} />
    </div>
  );
}

function SurfaceNode({ node, ctx }) {
  if (!node || typeof node !== 'object') return null;
  const Component = NODES[node.type];
  if (!Component) {
    // Newer producers may add node types within v1; draw the rest.
    return <div className="vela-surface-unknown">Cannot show this part.</div>;
  }
  const span =
    Number.isInteger(node.span) && node.span > 1
      ? { gridColumn: `span ${Math.min(node.span, 6)}` }
      : undefined;
  return <Component node={node} ctx={ctx} style={span} />;
}

function Children({ nodes, ctx }) {
  return (nodes || []).map((child, index) => (
    <SurfaceNode key={child?.id || index} node={child} ctx={ctx} />
  ));
}

/** The generic mark for an icon name this host does not know (Lucide names;
 *  Vela draws Phosphor). Nothing is ever fetched for an icon. */
function SurfaceIcon() {
  return (
    <span className="vela-surface-icon" aria-hidden="true">
      <Square size={14} weight="duotone" />
    </span>
  );
}

// ---- containers

function Stack({ node, ctx, style }) {
  const direction = node.direction === 'row' ? ' vela-surface-stack--row' : '';
  return (
    <div
      className={`vela-surface-stack vela-surface-gap--${node.gap || 'm'}${direction}`}
      style={style}
    >
      <Children nodes={node.children} ctx={ctx} />
    </div>
  );
}

function Grid({ node, ctx, style }) {
  const columns = Number.isInteger(node.columns) ? Math.max(1, Math.min(node.columns, 6)) : 2;
  return (
    <div className="vela-surface-grid" style={{ ...style, '--surface-cols': columns }}>
      <Children nodes={node.children} ctx={ctx} />
    </div>
  );
}

function Panel({ node, ctx, style }) {
  return (
    <div className="vela-surface-panel" data-tone={toneOf(node.tone)} style={style}>
      <Card plain title={node.title || null} icon={node.icon ? <SurfaceIcon /> : null}>
        <div className="vela-surface-stack vela-surface-gap--m">
          <Children nodes={node.children} ctx={ctx} />
        </div>
      </Card>
    </div>
  );
}

/**
 * Windows on a wallpaper with a dock. Which windows are minimized and which
 * has focus is the viewer's state, kept by window id so it survives document
 * refreshes; the surface only supplies the starting value.
 */
function Desktop({ node, ctx, style }) {
  const [minimized, setMinimized] = useState({});
  const [focused, setFocused] = useState(null);
  const isMin = (win) =>
    win.id && win.id in minimized ? minimized[win.id] : Boolean(win.minimized);
  const toggle = (id, value) => setMinimized((previous) => ({ ...previous, [id]: value }));
  const openFromDock = (id) => {
    toggle(id, false);
    setFocused(id);
  };

  const windows = (node.windows || []).filter((win) => win && typeof win === 'object');
  const preset = WALLPAPERS.includes(node.wallpaper) ? node.wallpaper : 'plain';
  return (
    <div className={`vela-synth vela-synth--${preset}`} style={style}>
      <div className="vela-synth-wallpaper">
        {node.title ? <div className="vela-synth-hostname">{node.title}</div> : null}
        <div className="vela-synth-windows">
          {windows
            .filter((win) => !isMin(win))
            .map((win, index) => (
              <SynthWindow
                key={win.id || index}
                node={win}
                ctx={ctx}
                focused={focused === win.id}
                onFocus={win.id ? () => setFocused(win.id) : undefined}
                onMinimize={win.id ? () => toggle(win.id, true) : undefined}
              />
            ))}
        </div>
      </div>
      <div className="vela-synth-taskbar">
        {(node.dock || []).map((item, index) => {
          // A dock item naming a window that is not there is inert, per the
          // contract: drawn, linked to nothing.
          const target =
            item.window && windows.some((win) => win.id === item.window) ? item.window : null;
          const targetWindow = target ? windows.find((win) => win.id === target) : null;
          const classes = [
            'vela-synth-task',
            target && targetWindow && isMin(targetWindow) ? 'is-minimized' : '',
          ]
            .filter(Boolean)
            .join(' ');
          const content = (
            <>
              {item.icon ? <SurfaceIcon /> : null}
              <span className="vela-synth-task-label">{item.label}</span>
              {item.badge ? <Tag tone={toneOf(item.tone)}>{item.badge}</Tag> : null}
            </>
          );
          return target ? (
            <button
              key={item.id || index}
              type="button"
              className={classes}
              data-tone={toneOf(item.tone)}
              title={item.label}
              onClick={() => openFromDock(target)}
            >
              {content}
            </button>
          ) : (
            <span key={item.id || index} className={classes} data-tone={toneOf(item.tone)}>
              {content}
            </span>
          );
        })}
        <SynthClock />
      </div>
    </div>
  );
}

const WALLPAPERS = ['plain', 'dusk', 'dawn', 'grid', 'ocean', 'forest'];

/** The taskbar's clock. It ticks locally: the time between two polls of the
 *  document is not something a server has to be asked about. */
function SynthClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 20000);
    return () => clearInterval(timer);
  }, []);
  return (
    <span className="vela-synth-clock">
      {new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(now)}
    </span>
  );
}

function SynthWindow({ node, ctx, focused, onFocus, onMinimize }) {
  const size = SIZES.includes(node.size) ? node.size : 'm';
  return (
    <div
      className={`vela-synth-window vela-synth-window--${size}${focused ? ' is-focused' : ''}`}
      data-tone={toneOf(node.tone)}
      onPointerDown={onFocus}
    >
      <div className="vela-synth-titlebar">
        {node.icon ? <SurfaceIcon /> : null}
        <span className="vela-synth-title">{node.title}</span>
        {onMinimize ? (
          <button
            type="button"
            className="vela-synth-minimize"
            onClick={onMinimize}
            aria-label={`Minimize ${node.title}`}
          >
            —
          </button>
        ) : null}
      </div>
      <div className="vela-synth-body">
        <Children nodes={node.children} ctx={ctx} />
      </div>
    </div>
  );
}

const SIZES = ['s', 'm', 'l', 'wide', 'tall'];

// ---- values

function Text({ node, style }) {
  const kind = ['body', 'title', 'caption', 'mono'].includes(node.style) ? node.style : 'body';
  return (
    <p
      className={`vela-surface-text vela-surface-text--${kind}`}
      data-tone={toneOf(node.tone)}
      style={style}
    >
      {node.value}
    </p>
  );
}

function StatNode({ node, style }) {
  return (
    <div className="vela-surface-stat" data-tone={toneOf(node.tone)} style={style}>
      {node.label ? <div className="vela-surface-label">{node.label}</div> : null}
      <Stat
        value={formatSurfaceValue(node.value, node.format)}
        unit={node.unit || null}
        delta={node.delta || null}
        deltaTone={toneOf(node.tone)}
        caption={node.caption || null}
      />
    </div>
  );
}

function Progress({ node, style }) {
  return (
    <div className="vela-surface-progress" style={style}>
      <Meter
        percent={node.value}
        label={node.label || null}
        detail={node.caption || null}
        tone={toneOf(node.tone)}
      />
    </div>
  );
}

/**
 * A list, in the desk's `vela-rows` drawing but without the desk's cap: a
 * surface may have more rows than a widget cell, and silently dropping them
 * is not a host's call to make.
 */
function List({ node, style }) {
  const rows = (node.rows || []).filter((row) => row && typeof row === 'object');
  if (!rows.length) return null;
  return (
    <ul className="vela-rows vela-surface-list" style={style}>
      {rows.map((row, index) => (
        <li className="vela-row" key={row.id || index}>
          {row.icon ? (
            <SurfaceIcon />
          ) : (
            <span className="vela-row-dot" data-tone={toneOf(row.tone)} aria-hidden="true" />
          )}
          <span className="vela-row-text">
            <span className="vela-row-label">{row.label}</span>
            {row.detail ? <span className="vela-row-detail">{row.detail}</span> : null}
          </span>
          {typeof row.progress === 'number' ? (
            <span className="vela-row-tail vela-surface-list-meter">
              <Meter percent={row.progress} />
            </span>
          ) : row.badge ? (
            <span className="vela-row-tail">
              <Tag tone={toneOf(row.tone)}>{row.badge}</Tag>
            </span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

/** The desk's key/value drawing, again without the widget cap. */
function KeyValueNode({ node, style }) {
  const rows = (node.rows || []).filter((row) => row && typeof row === 'object');
  if (!rows.length) return null;
  return (
    <dl className="vela-kv vela-surface-kv" style={style}>
      {rows.map((row, index) => (
        <div className="vela-kv-row" key={`${row.label}-${index}`}>
          <dt className="vela-kv-label">{row.label}</dt>
          <dd className="vela-kv-value" data-tone={toneOf(row.tone)}>
            {formatSurfaceValue(row.value, row.format)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Chart({ node, style }) {
  const series = (node.series || []).filter((value) => Number.isFinite(value));
  if (series.length < 2) return null;
  const domain =
    Array.isArray(node.domain) && node.domain.length === 2 && node.domain.every(Number.isFinite)
      ? node.domain
      : null;
  return (
    <figure className="vela-surface-chart" data-tone={toneOf(node.tone)} style={style}>
      {node.label ? <figcaption className="vela-surface-label">{node.label}</figcaption> : null}
      {node.kind === 'line' ? (
        <Sparkline series={series} domain={domain} tone={toneOf(node.tone)} label={node.label} />
      ) : (
        <Bars series={series} domain={domain} caption={node.caption || null} />
      )}
      {node.caption && node.kind === 'line' ? (
        <div className="vela-surface-caption">{node.caption}</div>
      ) : null}
    </figure>
  );
}

function Table({ node, style }) {
  const columns = (node.columns || []).filter((column) => column && column.key);
  if (!columns.length) return null;
  return (
    <div className="vela-surface-table-wrap" style={style}>
      <table className="vela-surface-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} className={`vela-surface-align--${column.align || 'start'}`}>
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {(node.rows || []).map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column.key} className={`vela-surface-align--${column.align || 'start'}`}>
                  {formatSurfaceValue(row?.[column.key], column.format)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Badge({ node, style }) {
  return (
    <span className="vela-surface-badge" style={style}>
      {node.icon ? <SurfaceIcon /> : null}
      <Tag tone={toneOf(node.tone)}>{node.label}</Tag>
    </span>
  );
}

/**
 * A button asks the host to run an action the document declared. This
 * milestone is read-only: with no `onAction` wired, an action button renders
 * nothing rather than a control that cannot do what it says.
 */
function Button({ node, ctx, style }) {
  const [state, setState] = useState('idle'); // idle | confirm | busy | done | error
  const [error, setError] = useState(null);
  const action = ctx.actions[node.action];
  if (!action || !ctx.onAction) return null;

  const run = async () => {
    setState('busy');
    setError(null);
    try {
      await ctx.onAction(action.id, node.input || {});
      setState('done');
      setTimeout(() => setState('idle'), 2500);
    } catch (problem) {
      setError(problem?.message || String(problem));
      setState('error');
    }
  };
  const mustConfirm = action.danger || action.confirm;

  if (state === 'confirm') {
    return (
      <span className="vela-surface-confirm" style={style}>
        <span>{action.confirm || `Run “${action.title}”?`}</span>
        <button
          type="button"
          className="vela-surface-button"
          data-tone={action.danger ? 'red' : undefined}
          onClick={run}
        >
          {node.label}
        </button>
        <button
          type="button"
          className="vela-surface-button vela-surface-button--ghost"
          onClick={() => setState('idle')}
        >
          Cancel
        </button>
      </span>
    );
  }
  return (
    <span className="vela-surface-action" style={style}>
      <button
        type="button"
        className="vela-surface-button"
        data-tone={toneOf(node.tone)}
        disabled={state === 'busy'}
        title={action.title}
        onClick={() => (mustConfirm ? setState('confirm') : run())}
      >
        {state === 'busy' ? 'Working…' : state === 'done' ? 'Done' : node.label}
      </button>
      {state === 'error' ? (
        <span className="vela-surface-caption" data-tone="red">
          {error}
        </span>
      ) : null}
    </span>
  );
}

const INLINE_IMAGE = /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}$/;

function Image({ node, style }) {
  // Inline PNG, JPEG or WebP only: a surface never makes the host fetch an
  // address, so anything else renders nothing at all.
  if (typeof node.src !== 'string' || !INLINE_IMAGE.test(node.src)) return null;
  const fit = node.fit === 'cover' ? 'cover' : 'contain';
  return (
    <img
      className={`vela-surface-image vela-surface-image--${fit}`}
      src={node.src}
      alt={node.alt || ''}
      width={node.width}
      height={node.height}
      style={style}
    />
  );
}

function Divider({ style }) {
  return <hr className="vela-surface-divider" style={style} />;
}

function Empty({ node, style }) {
  return (
    <div className="vela-surface-empty" style={style}>
      {node.icon ? <SurfaceIcon /> : null}
      {node.message}
    </div>
  );
}

const NODES = {
  stack: Stack,
  grid: Grid,
  panel: Panel,
  desktop: Desktop,
  text: Text,
  stat: StatNode,
  progress: Progress,
  list: List,
  keyvalue: KeyValueNode,
  chart: Chart,
  table: Table,
  badge: Badge,
  button: Button,
  image: Image,
  divider: Divider,
  empty: Empty,
};
