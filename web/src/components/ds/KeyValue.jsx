/**
 * Label left, value right. Up to eight rows.
 *
 * The plainest thing on the board and the one that carries the most. A row may
 * carry a tone, which tints its value only -- a wrong number should read as
 * wrong without the whole row turning red.
 */
const MAX_ROWS = 8;

export default function KeyValue({ rows = [], className = '', ...rest }) {
  const shown = rows.slice(0, MAX_ROWS);
  if (shown.length === 0) return null;
  const classes = ['vela-kv', className].filter(Boolean).join(' ');
  return (
    <dl className={classes} {...rest}>
      {shown.map((row, index) => (
        <div className="vela-kv-row" key={`${row.label}-${index}`}>
          <dt className="vela-kv-label">{row.label}</dt>
          <dd className="vela-kv-value" data-tone={row.tone}>
            {row.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
