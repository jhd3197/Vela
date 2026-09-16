import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowsClockwise } from '@phosphor-icons/react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { api, relTime } from '../api.js';
import { useApps } from '../store.jsx';
import Button from './ui/Button.jsx';
import Dialog from './ui/Dialog.jsx';

// Settings › Updates. The copy has to be exact about what leaves this
// computer, because one request a day to a third party is the only network
// call Vela makes on its own behalf — and the switch that stops it sits beside
// the sentence that describes it.
const DOWNLOADS = 'https://github.com/jhd3197/vela/releases/latest';

// Release notes are written by the maintainer, but they are rendered as
// untrusted text all the same: images are dropped rather than fetched, and
// links open in a new tab with no referrer.
const MARKDOWN_COMPONENTS = {
  img: () => null,
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  ),
};

const CAPABILITY_NOTE = {
  installer: 'This copy was installed with the Windows installer.',
  portable: 'This is the portable Windows folder.',
  tarball: 'This copy was unpacked from a .tar.gz.',
  source: 'This is a source checkout, so `git pull` is how it updates.',
  container: 'This runs inside a container, so a new image tag is the update.',
};

// Capabilities Vela can install for you. The others get the download and an
// honest explanation instead of a button that would do nothing.
const CAN_INSTALL = new Set(['installer', 'portable', 'tarball']);

const WORKING_STATES = new Set([
  'downloading',
  'verifying',
  'backing-up',
  'applying',
  'restarting',
]);

const STEP_LABEL = {
  downloading: 'Downloading…',
  verifying: 'Checking the download…',
  'backing-up': 'Backing up first…',
  applying: 'Installing…',
  restarting: 'Vela is restarting…',
};

// While Vela restarts, its own dashboard is talking to a server that is going
// away and coming back. Polling /api/health is how the page knows it is back —
// and the version it answers with is how it knows the update worked.
function useRestartWatch(active, expected, onBack) {
  useEffect(() => {
    if (!active) return undefined;
    let cancelled = false;
    const timer = setInterval(async () => {
      try {
        const health = await api.health();
        if (cancelled) return;
        // Answering at all means it is up; the version says which one.
        onBack(health?.version);
      } catch {
        // Still down. That is expected for a while.
      }
    }, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [active, expected, onBack]);
}

export default function UpdatesSection({ onPendingChange }) {
  const { pushToast } = useApps();
  const [status, setStatus] = useState(null);
  const [job, setJob] = useState(null);
  const [report, setReport] = useState(null);
  const [checking, setChecking] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [starting, setStarting] = useState(false);
  const cancelRef = useRef(null);

  const busy = checking || saving || starting;
  useEffect(() => {
    onPendingChange?.('updates', busy);
    return () => onPendingChange?.('updates', false);
  }, [busy, onPendingChange]);

  const refresh = useCallback(() => {
    api
      .getUpdates()
      .then(setStatus)
      .catch(() => setStatus(null));
    api
      .updateJob()
      .then(setJob)
      .catch(() => setJob(null));
  }, []);

  useEffect(() => {
    refresh();
    // What happened to the update that ran before this start, if any.
    api
      .updateReport()
      .then((value) => setReport(value && value.outcome ? value : null))
      .catch(() => setReport(null));
  }, [refresh]);

  // While an update runs, its progress belongs to the engine, so the page
  // asks rather than assuming what the click it made is doing.
  useEffect(() => {
    if (!WORKING_STATES.has(job?.state)) return undefined;
    const timer = setInterval(() => {
      api
        .updateJob()
        .then(setJob)
        .catch(() => {
          // Vela is going away. The restart watch takes over from here.
        });
    }, 1000);
    return () => clearInterval(timer);
  }, [job?.state]);

  // Two different things: the overlay covers the whole update, because there
  // is nothing useful to do on this page while it runs. Polling /api/health
  // starts only once Vela is actually going away — during the download the
  // server is still answering, and a poll then would reload the page in the
  // middle of it.
  const working = WORKING_STATES.has(job?.state);
  const restarting = job?.state === 'applying' || job?.state === 'restarting';
  const onBack = useCallback((version) => {
    setJob(null);
    // The server that answered is the one to reload against.
    if (version) location.reload();
  }, []);
  useRestartWatch(restarting, status?.latest, onBack);

  const check = async () => {
    setChecking(true);
    try {
      setStatus(await api.checkUpdates());
    } catch (error) {
      pushToast(error.message || 'Vela could not check for updates.');
    } finally {
      setChecking(false);
    }
  };

  const save = async (change) => {
    setSaving(true);
    try {
      await api.updateSettings({ updates: change });
      refresh();
    } catch (error) {
      pushToast(error.message || 'Could not save that preference.');
    } finally {
      setSaving(false);
    }
  };

  const install = async () => {
    setStarting(true);
    try {
      setJob(await api.applyUpdate());
      setConfirming(false);
    } catch (error) {
      pushToast(error.message || 'Vela could not install the update.');
      refresh();
    } finally {
      setStarting(false);
    }
  };

  const rollback = async () => {
    setStarting(true);
    try {
      setJob(await api.rollbackUpdate());
    } catch (error) {
      pushToast(error.message || 'Vela could not go back.');
    } finally {
      setStarting(false);
    }
  };

  const installable = CAN_INSTALL.has(status?.capability);

  return (
    <section className="panel" id="settings-updates">
      <div className="panel-head">
        <h2>Updates</h2>
        <Button size="small" pending={checking} disabled={!status?.check || busy} onClick={check}>
          <ArrowsClockwise size={14} aria-hidden="true" />
          Check now
        </Button>
      </div>

      {/* What the last update did, said once on the first visit after it. */}
      {report && (
        <p
          className={report.outcome === 'updated' ? 'saved-note' : 'inline-error'}
          role={report.outcome === 'updated' ? 'status' : 'alert'}
        >
          {report.outcome === 'updated'
            ? `Updated to ${report.to}.`
            : report.outcome === 'failed'
              ? `The update to ${report.to} did not finish, and Vela is still on ${report.from}. Nothing was lost — your data was backed up first.`
              : `The last update stopped: ${report.message}`}
        </p>
      )}

      <dl className="fact-grid">
        <div className="fact">
          <dt>This server</dt>
          <dd className="mono">{status?.current || '—'}</dd>
        </div>
        <div className="fact">
          <dt>Latest release</dt>
          <dd className="mono">{status?.latest || 'Not checked'}</dd>
        </div>
        <div className="fact">
          <dt>Last checked</dt>
          <dd>{status?.checkedAt ? relTime(status.checkedAt) : 'Never'}</dd>
        </div>
      </dl>

      {status?.error && (
        <p className="panel-note">
          The last check did not get through: {status.error}. Vela kept what it knew before.
        </p>
      )}

      {/* The switch, next to the sentence that says exactly what it stops. */}
      <div className="settings-row">
        <div>
          <h3 id="update-check-label">Check for new versions</h3>
          <p>
            Once a day, Vela asks GitHub which release is newest — one anonymous request to
            github.com. It sends no identifier, no version report and nothing about your apps or
            your data. Turn this off and Vela makes no request at all; you can still check by hand
            from the releases page.
          </p>
        </div>
        <button
          className={`switch${status?.check ? ' switch-on' : ''}`}
          role="switch"
          aria-checked={Boolean(status?.check)}
          aria-labelledby="update-check-label"
          disabled={busy || !status}
          onClick={() => save({ check: !status.check })}
        />
      </div>

      {/* Installing without being asked is a bigger step than noticing, so it
          is its own choice and it is off until someone makes it. */}
      {status?.check && installable && (
        <div className="settings-row">
          <div>
            <h3 id="update-mode-label">When an update is available</h3>
            <p>
              Installing automatically happens at {String(status.hour).padStart(2, '0')}:00, and
              only when nothing is running: no open app, no automation, and no failing health check.
              Otherwise Vela waits and tries the next day.
            </p>
          </div>
          <div className="seg" role="group" aria-labelledby="update-mode-label">
            {[
              ['notify', 'Tell me'],
              ['auto', 'Install automatically'],
            ].map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={status.mode === value}
                disabled={busy}
                className={`seg-opt${status.mode === value ? ' seg-opt-active' : ''}`}
                onClick={() => save({ mode: value })}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      )}

      {status?.available ? (
        <>
          <div className="update-banner">
            <h3>Vela {status.latest} is available</h3>
            <p className="panel-note">
              You are running {status.current}. {CAPABILITY_NOTE[status.capability] || ''}
            </p>
            <div className="actions">
              {installable ? (
                <Button
                  variant="primary"
                  disabled={busy || restarting}
                  onClick={() => setConfirming(true)}
                >
                  Update now
                </Button>
              ) : (
                <a className="btn btn-primary" href={DOWNLOADS} target="_blank" rel="noreferrer">
                  {status.asset ? `Download ${status.asset.name}` : 'Open the releases page'}
                </a>
              )}
            </div>
          </div>
          {status.notes && (
            <div className="update-notes">
              <h3>Release notes</h3>
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
                {status.notes}
              </ReactMarkdown>
            </div>
          )}
        </>
      ) : (
        <p className="panel-note">
          {!status?.check
            ? 'Vela is not checking for new versions.'
            : status?.checkedAt
              ? `Vela ${status.current} is the latest release.`
              : 'Vela has not checked yet.'}
        </p>
      )}

      {job?.state === 'error' && (
        <p className="inline-error" role="alert">
          {job.message}
        </p>
      )}

      {/* Rolling back is offered only where there is a previous version kept. */}
      {job?.rollback && (
        <div className="actions">
          <Button disabled={busy || working} onClick={rollback}>
            Go back to the previous version
          </Button>
        </div>
      )}

      {/* Installing replaces Vela with another copy of Vela, so it says what
          will happen before it starts. */}
      <Dialog
        open={confirming}
        onClose={() => setConfirming(false)}
        pending={starting}
        initialFocusRef={cancelRef}
      >
        <h2>Install Vela {status?.latest}?</h2>
        <p className="panel-note">
          Vela will download the release, check it against its published checksum, back itself up,
          then close and reopen as the new version. Your apps and everything they saved are not
          touched. This usually takes under a minute; the page will reconnect on its own.
        </p>
        <div className="actions">
          <Button ref={cancelRef} disabled={starting} onClick={() => setConfirming(false)}>
            Cancel
          </Button>
          <Button variant="primary" pending={starting} onClick={install}>
            Install it
          </Button>
        </div>
      </Dialog>

      {/* The restart overlay. Not a dialog to dismiss: there is nothing to do
          but wait, and the page reloads itself when Vela answers again. */}
      {working && (
        <div className="update-overlay" role="status" aria-live="assertive">
          <div className="update-overlay-card">
            <div className="spinner" aria-hidden="true" />
            <h2>{STEP_LABEL[job.state] || 'Updating…'}</h2>
            {typeof job.percent === 'number' && job.state === 'downloading' && (
              <div
                className="update-progress"
                role="progressbar"
                aria-valuenow={job.percent}
                aria-valuemin={0}
                aria-valuemax={100}
              >
                <span style={{ width: `${job.percent}%` }} />
              </div>
            )}
            <p className="panel-note">
              Vela is closing and reopening as {status?.latest}. This page reconnects on its own.
            </p>
          </div>
        </div>
      )}
    </section>
  );
}
