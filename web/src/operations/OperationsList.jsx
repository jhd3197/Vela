import { Link } from 'react-router-dom';
import { relTime } from '../api.js';
import { WidgetStatus } from '../desk/widgets/primitives.jsx';
import { statusDotState, statusLabel } from './status.js';

/**
 * A list of background work, on the desk's own status primitive.
 *
 * It takes operations in the shape `operations/normalize.js` produces and
 * nothing else, so the System page, the desk widget and anything added later
 * are looking at the same rows in the same words.
 */
export default function OperationsList({ operations = [], empty = 'Nothing is running.' }) {
  if (operations.length === 0) return <p className="panel-note">{empty}</p>;
  return (
    <WidgetStatus
      cells={operations.map((item) => ({
        id: item.key,
        name: item.title,
        state: statusDotState(item.status),
        meta: [item.subtitle || statusLabel(item.status), item.updatedAt && relTime(item.updatedAt)]
          .filter(Boolean)
          .join(' · '),
        lead: (
          <Link className="desk-status-open" to={item.href} aria-label={`Open ${item.title}`}>
            <span className="desk-status-dot" data-state={statusDotState(item.status)} />
          </Link>
        ),
      }))}
    />
  );
}
