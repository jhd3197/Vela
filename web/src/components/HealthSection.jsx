import { useCallback, useEffect, useState } from 'react';
import { ArrowsClockwise } from '@phosphor-icons/react';
import { api, relTime } from '../api.js';
import { useApps } from '../store.jsx';
import Button from './ui/Button.jsx';

// Settings › Health. Opening this only reads the last sweep — thirteen checks
// should not run because someone opened a settings popup — so "Run now" is the
// deliberate action, and until it happens the section says so.
//
// Adapted from ServerKit's `dashboard/SetupHealthWidget.jsx` (MIT, same owner),
// which renders a fixed list of setup steps; this renders whatever the engine
// reports, so a check added later needs no dashboard change.
const DOT = { ok: 'status-dot-ok', warn: 'status-dot-warn', fail: 'status-dot-bad' };

export default function HealthSection({ onPendingChange }) {
  const { pushToast } = useApps();
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);
  const [repairing, setRepairing] = useState(null);

  useEffect(() => {
    onPendingChange?.('health', running || repairing !== null);
    return () => onPendingChange?.('health', false);
  }, [running, repairing, onPendingChange]);

  useEffect(() => {
    api
      .getDoctor()
      .then(setResult)
      .catch(() => setResult(null));
  }, []);

  const run = useCallback(async () => {
    setRunning(true);
    try {
      setResult(await api.runDoctor());
    } catch (error) {
      pushToast(error.message || 'Vela could not run its checks.');
    } finally {
      setRunning(false);
    }
  }, [pushToast]);

  const repair = async (key) => {
    setRepairing(key);
    try {
      const outcome = await api.repairDoctor(key);
      // The engine hands back the check as it now stands, so the row updates
      // without a second sweep.
      setResult((previous) =>
        previous
          ? {
              ...previous,
              checks: previous.checks.map((check) => (check.key === key ? outcome.check : check)),
            }
          : previous,
      );
      pushToast(outcome.detail || 'Repaired.', outcome.ok ? 'success' : 'error');
    } catch (error) {
      pushToast(error.message || 'That repair did not work.');
    } finally {
      setRepairing(null);
    }
  };

  const checks = result?.checks || [];
  const summary = result?.summary;
  const shown = checks.filter((check) => check.status !== 'skipped');
  const skipped = checks.length - shown.length;

  return (
    <section className="panel" id="settings-health">
      <div className="panel-head">
        <h2>Health</h2>
        <Button size="small" pending={running} onClick={run}>
          <ArrowsClockwise size={14} aria-hidden="true" />
          Run now
        </Button>
      </div>

      <p className="panel-note">
        {summary?.text || 'Vela has not checked itself yet.'}
        {result?.ranAt ? ` Last run ${relTime(result.ranAt)}.` : ''}
      </p>

      {checks.length === 0 ? (
        <p className="panel-note">
          Vela checks its data directory, the apps it is running, its backups and the runtimes it
          needs. Nothing is sent anywhere — every check looks at this computer.
        </p>
      ) : (
        <ul className="health-list">
          {shown.map((check) => (
            <li key={check.key} className={`health-row health-row-${check.status}`}>
              <span
                className={`status-dot ${DOT[check.status] || ''}`}
                role="img"
                aria-label={check.status === 'ok' ? 'Passed' : `Needs attention: ${check.status}`}
              />
              <span className="health-text">
                <span className="health-title">{check.title}</span>
                <span className="health-detail">{check.detail}</span>
              </span>
              {check.repairable && (
                <Button
                  size="small"
                  variant="ghost"
                  pending={repairing === check.key}
                  disabled={running}
                  onClick={() => repair(check.key)}
                >
                  Repair
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}

      {skipped > 0 && (
        <p className="panel-note">
          {skipped} check{skipped === 1 ? '' : 's'} did not apply to this server and{' '}
          {skipped === 1 ? 'was' : 'were'} skipped.
        </p>
      )}
    </section>
  );
}
