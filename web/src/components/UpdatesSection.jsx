import { useCallback, useEffect, useState } from 'react';
import { ArrowsClockwise } from '@phosphor-icons/react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { api, relTime } from '../api.js';
import { useApps } from '../store.jsx';
import Button from './ui/Button.jsx';

// Settings › Updates. The copy has to be exact about what leaves this
// computer, because one request a day to a third party is the only network
// call Vela makes on its own behalf — and the switch that stops it has to be
// right here beside the sentence that describes it.
//
// What an update *does* is Stage 6. Until then this offers the download.
const DOWNLOADS = 'https://github.com/jhd3197/vela/releases/latest';

// Release notes are written by the maintainer, but they are rendered here as
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

export default function UpdatesSection({ onPendingChange }) {
  const { pushToast } = useApps();
  const [status, setStatus] = useState(null);
  const [checking, setChecking] = useState(false);
  const [saving, setSaving] = useState(false);

  const busy = checking || saving;
  useEffect(() => {
    onPendingChange?.('updates', busy);
    return () => onPendingChange?.('updates', false);
  }, [busy, onPendingChange]);

  const refresh = useCallback(() => {
    api
      .getUpdates()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

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

  return (
    <section className="panel" id="settings-updates">
      <div className="panel-head">
        <h2>Updates</h2>
        <Button size="small" pending={checking} disabled={!status?.check} onClick={check}>
          <ArrowsClockwise size={14} aria-hidden="true" />
          Check now
        </Button>
      </div>

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

      {status?.available ? (
        <>
          <div className="update-banner">
            <h3>Vela {status.latest} is available</h3>
            <p className="panel-note">
              You are running {status.current}. {CAPABILITY_NOTE[status.capability] || ''}
            </p>
            <div className="actions">
              {/* Installing from here arrives in the next release; until then
                  the honest offer is the download. */}
              <a className="btn btn-primary" href={DOWNLOADS} target="_blank" rel="noreferrer">
                {status.asset ? `Download ${status.asset.name}` : 'Open the releases page'}
              </a>
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
    </section>
  );
}
