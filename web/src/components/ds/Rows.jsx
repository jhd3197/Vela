/**
 * A list of things, each with a name, a line of detail and a trailing note.
 *
 * What "Running now" and "Needs you" are made of. A row leads with a dot in its
 * own tone unless it brings something else to lead with, and may end with a
 * time or a control the widget puts there.
 */
const MAX_ROWS = 8;

export default function Rows({ rows = [], className = '', ...rest }) {
  const shown = rows.slice(0, MAX_ROWS);
  if (shown.length === 0) return null;
  const classes = ['vela-rows', className].filter(Boolean).join(' ');
  return (
    <ul className={classes} {...rest}>
      {shown.map((row, index) => (
        <li className="vela-row" key={row.id ?? `${row.label}-${index}`}>
          {row.lead ?? <span className="vela-row-dot" data-tone={row.tone} aria-hidden="true" />}
          <span className="vela-row-text">
            <span className="vela-row-label">{row.label}</span>
            {row.detail ? <span className="vela-row-detail">{row.detail}</span> : null}
          </span>
          {row.tail ? <span className="vela-row-tail">{row.tail}</span> : null}
        </li>
      ))}
    </ul>
  );
}
