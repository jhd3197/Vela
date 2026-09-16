import { useEffect, useRef, useState } from 'react';
import Dialog from './ui/Dialog.jsx';
import { api } from '../api.js';

// What each permission means in the user's words. A capability the engine
// grants but this list has not named yet falls back to its own id rather than
// disappearing from the review.
const CAPABILITY_TEXT = {
  storage: 'Keep its own data on this computer',
  connections: 'Use a connection you have set up',
  actions: 'Ask other apps to do things you allow',
  widgets: 'Show summaries on your desk',
};

export function describeCapabilities(capabilities, widgets) {
  if (!capabilities?.length) return 'None';
  return capabilities
    .map((capability) => {
      const text = CAPABILITY_TEXT[capability] || capability;
      if (capability !== 'widgets' || !widgets?.length) return text;
      return `${text} (${widgets.map((widget) => widget.name).join(', ')})`;
    })
    .join('; ');
}

export default function ReleaseReview({ source, onClose, onComplete }) {
  const cancelButton = useRef(null);
  const token = useRef(null);
  const [review, setReview] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [approved, setApproved] = useState(false);
  useEffect(() => {
    let cancelled = false;
    api
      .prepareRelease(source)
      .then((value) => {
        if (cancelled) {
          api.cancelRelease(value.review).catch(() => {});
          return;
        }
        token.current = value.review;
        setReview(value);
      })
      .catch((failure) => {
        if (!cancelled) setError(failure.message);
      });
    return () => {
      cancelled = true;
      if (token.current) api.cancelRelease(token.current).catch(() => {});
    };
  }, [source]);
  return (
    <Dialog
      open
      onClose={onClose}
      pending={busy}
      initialFocusRef={cancelButton}
      className="release-dialog"
      aria-labelledby="release-title"
    >
      <h2 id="release-title">{review?.rollback ? 'Review rollback' : 'Review release'}</h2>
      {!review && !error && (
        <p role="status">Checking package, permissions and data compatibility…</p>
      )}
      {review && (
        <>
          <p className="release-name">
            {review.name}{' '}
            <span>
              {review.previousVersion ? `${review.previousVersion} → ` : ''}
              {review.version}
            </span>
          </p>
          <dl>
            <dt>Source</dt>
            <dd>
              {review.source.publisher} ·{' '}
              {review.source.kind === 'catalog'
                ? 'Pinned catalog archive'
                : review.source.kind === 'rollback'
                  ? 'Retained package'
                  : 'Local package; publisher not verified'}
            </dd>
            {review.trustedLegacy && (
              <>
                <dt>Legacy trust</dt>
                <dd>
                  This retained v1 app shares the hub origin and browser storage. It does not use
                  the v2 sandbox.
                </dd>
              </>
            )}
            <dt>Permissions</dt>
            <dd>{describeCapabilities(review.capabilities, review.widgets)}</dd>
            {review.newCapabilities.length > 0 && (
              <>
                <dt>New permissions</dt>
                <dd>{describeCapabilities(review.newCapabilities, review.widgets)}</dd>
              </>
            )}
            {review.operations.length > 0 && (
              <>
                <dt>Connection operations</dt>
                <dd>{review.operations.join(', ')}</dd>
              </>
            )}
            <dt>Data</dt>
            <dd>
              {review.rollback
                ? 'Restore the data checkpoint that matches this package. Changes since that checkpoint will be replaced and retained in recovery history.'
                : review.dataChanges
                  ? 'Migrate the current data after making a recovery checkpoint.'
                  : 'Keep the current app data.'}
            </dd>
          </dl>
          <details>
            <summary>Package verification</summary>
            <p className="mono release-digest">SHA-256 of staged files: {review.digest}</p>
            <p>
              Review expires in 20 minutes. Changes to the installed package or saved data require a
              new review.
            </p>
          </details>
          <label className="release-consent">
            <input
              type="checkbox"
              checked={approved}
              onChange={(event) => setApproved(event.target.checked)}
              disabled={busy}
            />
            Allow these permissions and{' '}
            {review.rollback ? 'restore this package and data' : 'install this release'}.
          </label>
        </>
      )}
      {error && <p role="alert">{error}</p>}
      <div className="actions">
        <button ref={cancelButton} type="button" className="btn" onClick={onClose} disabled={busy}>
          Cancel
        </button>
        {review && (
          <button
            className="btn btn-primary"
            disabled={busy || !approved || !!error}
            onClick={async () => {
              setBusy(true);
              setError('');
              try {
                await api.commitRelease(review);
                token.current = null;
                onComplete();
              } catch (failure) {
                setError(failure.message + ' Close this review and try again.');
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? 'Applying…' : review.rollback ? 'Restore release' : 'Install release'}
          </button>
        )}
      </div>
    </Dialog>
  );
}
