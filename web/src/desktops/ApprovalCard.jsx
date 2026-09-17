// A change waiting for you.
//
// Everything shown here was written by Vela from the request itself — never by
// the agent, and never from anything on the page it is looking at. That is the
// point of the separation: the sentence somebody makes a decision from must not
// be a sentence the thing asking for permission got to write.
//
// This control lives in the owner's window. Nothing inside the agent's browser
// can reach it, and approving is an owner-authenticated request on a route no
// app session has. A page that rendered an Approve button would be a page that
// could press it.
import { useState } from 'react';
import Button from '../components/ui/Button.jsx';
import { desktopsApi } from './desktopsApi.js';

function remaining(expiresAt) {
  const seconds = Math.round(Number(expiresAt) * 1000 - Date.now()) / 1000;
  if (!Number.isFinite(seconds) || seconds <= 0) return 'expiring now';
  if (seconds < 90) return `${Math.round(seconds)} seconds left`;
  return `${Math.round(seconds / 60)} minutes left`;
}

export default function ApprovalCard({ desktopId, request, onResolved }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  const resolve = async (decision, scopeFuture = false) => {
    setBusy(decision + (scopeFuture ? '-scope' : ''));
    setError(null);
    try {
      // The digest of what is on screen goes back with the decision, so a
      // prompt that was replaced between being rendered and being answered
      // resolves nothing.
      await desktopsApi.resolveApproval(desktopId, request.requestId, {
        decision,
        requestDigest: request.requestDigest || undefined,
        scopeFuture,
      });
      onResolved?.();
    } catch (problem) {
      setError(problem);
    } finally {
      setBusy(null);
    }
  };

  const summary = request.summary || {};
  return (
    <section className="approval-card" aria-labelledby={`approval-${request.requestId}`}>
      <h3 id={`approval-${request.requestId}`}>{summary.headline}</h3>
      {summary.detail?.length ? (
        <ul className="approval-detail">
          {summary.detail.map((line, index) => (
            <li key={`${request.requestId}-${index}`}>{line}</li>
          ))}
        </ul>
      ) : null}
      {summary.complete === false && (
        <p className="approval-note">
          This is more than Vela can list in full. What is shown is part of a larger change.
        </p>
      )}
      <p className="approval-note">
        {request.appName || request.appId} · {remaining(request.expiresAt)}
      </p>
      {error && (
        <p role="alert" className="approval-note">
          {error.message}
        </p>
      )}
      <div className="approval-actions">
        <Button
          variant="primary"
          pending={busy === 'approve'}
          disabled={Boolean(busy)}
          onClick={() => resolve('approve')}
        >
          Allow this
        </Button>
        <Button
          variant="danger"
          pending={busy === 'deny'}
          disabled={Boolean(busy)}
          onClick={() => resolve('deny')}
        >
          No
        </Button>
        <Button
          variant="ghost"
          size="small"
          pending={busy === 'approve-scope'}
          disabled={Boolean(busy)}
          onClick={() => resolve('approve', true)}
          title="Allow this kind of change on this desktop for the next hour"
        >
          Allow for an hour
        </Button>
      </div>
    </section>
  );
}
