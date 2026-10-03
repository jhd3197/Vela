import { Children, useId } from 'react';
import { CaretRight } from '@phosphor-icons/react';
import Switch from '../ui/Switch.jsx';

// The one way a settings surface is laid out. Every section of Settings is a
// column of groups; every group is a heading, a card of rows and, below the
// card, its actions and its status line. A row is its title and description on
// the left and its control on the right; anything bigger than a control (a
// form, a QR code, a grid of choices) goes in the row's body underneath.
//
// The rules this enforces, so nobody has to remember them:
//   - a yes/no setting is a `toggle`, never On/Off buttons or a checkbox;
//   - a read-only fact is a row with a `value`, never a separate fact grid;
//   - a text field is a row whose `htmlFor` names it, so the title labels it;
//   - an error or a confirmation is a `SettingsStatus` in the group's footer,
//     under the thing it is about, not above the settings.
// The styles live in `styles/components/_settings-kit.scss` and nowhere else.

// `{open && <Thing />}` leaves `false` behind; only something that renders
// earns a card or a row body, or the empty box adds its spacing anyway.
const hasContent = (children) => Children.toArray(children).length > 0;

/** The column of groups that makes up one settings section. */
export function SettingsPage({ className = '', children, ...props }) {
  return (
    <div {...props} className={`set-page${className ? ` ${className}` : ''}`}>
      {children}
    </div>
  );
}

/**
 * A titled card of rows. `aside` sits beside the title (a status pill, a
 * section-wide action); `footer` sits under the card (Save, a status line).
 */
export function SettingsGroup({
  id,
  title,
  description,
  aside,
  footer,
  headingRef,
  className = '',
  children,
}) {
  const headingId = useId();
  return (
    <section
      id={id}
      className={`set-group${className ? ` ${className}` : ''}`}
      aria-labelledby={title ? headingId : undefined}
    >
      {(title || aside || description) && (
        <header className="set-group-head">
          <div className="set-group-heading">
            {title && (
              <h3 id={headingId} ref={headingRef} tabIndex={headingRef ? -1 : undefined}>
                {title}
              </h3>
            )}
            {description && <div className="set-group-desc">{description}</div>}
          </div>
          {aside && <div className="set-group-aside">{aside}</div>}
        </header>
      )}
      {hasContent(children) && <div className="set-card">{children}</div>}
      {hasContent(footer) && <div className="set-group-foot">{footer}</div>}
    </section>
  );
}

/**
 * One setting. Give it exactly one of:
 *   `toggle`  `{ checked, onChange, disabled }` — renders the switch, named by the title;
 *   `control` any control, aligned to the right (name it with `htmlFor` or `titleId`);
 *   `value`   a read-only value;
 *   `onClick` the whole row opens a deeper screen (`value` then shows its state).
 * `lead` is an icon or status dot before the title; `children` is the body.
 */
export function SettingRow({
  title,
  description,
  control,
  toggle,
  value,
  mono = false,
  onClick,
  disabled = false,
  htmlFor,
  titleId,
  lead,
  stacked = false,
  className = '',
  children,
}) {
  const generatedId = useId();
  const labelId = titleId || generatedId;
  const classes = [
    'set-row',
    lead && 'set-row-has-lead',
    stacked && 'set-row-stacked',
    value !== undefined && !onClick && 'set-row-fact',
    onClick && 'set-row-link',
    className,
  ]
    .filter(Boolean)
    .join(' ');
  const valueNode =
    value !== undefined && value !== null ? (
      <span className={`set-value${mono ? ' mono' : ''}`}>{value}</span>
    ) : null;
  const text = (
    <span className="set-row-text">
      {htmlFor ? (
        <label className="set-row-title" id={labelId} htmlFor={htmlFor}>
          {title}
        </label>
      ) : (
        <span className="set-row-title" id={labelId}>
          {title}
        </span>
      )}
      {description && <span className="set-row-desc">{description}</span>}
    </span>
  );

  if (onClick) {
    return (
      <button type="button" className={classes} disabled={disabled} onClick={onClick}>
        {lead && <span className="set-row-lead">{lead}</span>}
        {text}
        <span className="set-row-control">
          {valueNode}
          <CaretRight size={16} aria-hidden="true" className="set-row-caret" />
        </span>
      </button>
    );
  }

  return (
    <div className={classes}>
      {lead && <span className="set-row-lead">{lead}</span>}
      {text}
      {(toggle || control || valueNode) && (
        <div className="set-row-control">
          {valueNode}
          {control}
          {toggle && (
            <Switch
              checked={toggle.checked}
              disabled={toggle.disabled}
              onChange={toggle.onChange}
              aria-labelledby={labelId}
            />
          )}
        </div>
      )}
      {hasContent(children) && <div className="set-row-body">{children}</div>}
    </div>
  );
}

/**
 * The outcome of the last thing done in a group: saved, failed, working. One
 * look for every section, announced politely unless it is an error.
 */
export function SettingsStatus({ tone = 'info', children }) {
  if (!children) return null;
  return (
    <p className={`set-status set-status-${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      {children}
    </p>
  );
}

/** A sentence of explanation under a group, after its card. */
export function SettingsNote({ children }) {
  return <p className="set-note">{children}</p>;
}

/** Buttons that act on the whole group, in the footer. */
export function SettingsActions({ children }) {
  return <div className="set-actions">{children}</div>;
}
