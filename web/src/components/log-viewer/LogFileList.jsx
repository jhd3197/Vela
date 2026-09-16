import { Link } from 'react-router-dom';
import { formatBytes } from '../../api.js';
import { groupLogs } from './logHelpers.js';

// The files on the left, grouped by what wrote them. Rotated siblings
// (`server.log.1`) sit under the log they came from rather than looking like
// separate logs. Adapted from ServerKit's `log-viewer/LogFileList.jsx`
// (MIT, same owner).
export default function LogFileList({ logs, selected, onSelect }) {
  const groups = groupLogs(logs || []);

  return (
    <nav className="logs-sidebar" aria-label="Logs">
      {groups.map((group) => (
        <section key={group.kind} className="logs-group">
          <h3 className="logs-group-title">{group.label}</h3>
          <ul className="logs-files">
            {group.entries.map((log) => (
              <li key={log.name}>
                <button
                  type="button"
                  className={`logs-file${selected === log.name ? ' logs-file-active' : ''}${
                    log.rotated ? ' logs-file-rotated' : ''
                  }`}
                  aria-current={selected === log.name ? 'true' : undefined}
                  onClick={() => onSelect(log.name)}
                >
                  <span className="logs-file-name">{log.name}</span>
                  <span className="logs-file-meta">{formatBytes(log.size)}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}

      {/* Automation runs already have a viewer with their steps and outcomes.
          Pointing at it beats showing the same runs a second time here. */}
      <section className="logs-group">
        <h3 className="logs-group-title">Elsewhere</h3>
        <ul className="logs-files">
          <li>
            <Link className="logs-file logs-file-link" to="/automations">
              <span className="logs-file-name">Automation runs</span>
              <span className="logs-file-meta">Open</span>
            </Link>
          </li>
        </ul>
      </section>
    </nav>
  );
}
