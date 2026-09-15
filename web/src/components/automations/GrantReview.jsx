import { useId, useRef } from 'react';
import { ShieldCheck, Warning } from '@phosphor-icons/react';
import Button from '../ui/Button.jsx';

// Vela shows the exact app, action and inputs an automation asks for before any
// of it can run. An approval inside a workflow is a decision about one run; this
// is the separate, durable permission to touch another app's data at all.
export default function GrantReview({ grants, pending, onAllow, onRevoke, error }) {
  const headingId = useId();
  if (!grants?.length) return null;
  return (
    <section className="panel automation-grants" aria-labelledby={headingId}>
      <h2 id={headingId} className="section-head">
        <ShieldCheck size={15} weight="fill" /> What this automation may change
      </h2>
      {error && (
        <p role="alert" className="automation-error">
          {error.message}
        </p>
      )}
      <ul className="automation-grant-list">
        {grants.map((grant) => (
          <GrantRow
            key={`${grant.app}:${grant.action}`}
            grant={grant}
            pending={pending}
            onAllow={onAllow}
            onRevoke={onRevoke}
          />
        ))}
      </ul>
    </section>
  );
}

function GrantRow({ grant, pending, onAllow, onRevoke }) {
  const allowButton = useRef(null);
  const name = grant.appName || grant.app;
  const title = grant.title || grant.action;
  return (
    <li className={`automation-grant${grant.granted ? ' automation-grant-allowed' : ''}`}>
      <div className="automation-grant-text">
        <span className="automation-grant-title">
          {name} · {title}
        </span>
        {grant.available ? (
          <span className="automation-grant-sub">
            {grant.effect === 'write' ? 'Adds a new record to' : 'Reads from'} {name}
            {grant.inputs?.length ? ` · sends ${grant.inputs.join(', ')}` : ''} · used by{' '}
            {grant.nodes.length} step{grant.nodes.length === 1 ? '' : 's'}
          </span>
        ) : (
          <span className="automation-grant-sub automation-grant-warn">
            <Warning size={13} /> {grant.error}
          </span>
        )}
        {grant.stale && (
          <span className="automation-grant-sub automation-grant-warn">
            <Warning size={13} /> {grant.staleReason} Review it again to keep this step working.
          </span>
        )}
      </div>
      {grant.available && (
        <div className="automation-grant-actions">
          {grant.granted ? (
            <Button
              size="small"
              variant="ghost"
              pending={pending}
              onClick={() => onRevoke(grant)}
              aria-label={`Stop allowing ${name} ${title}`}
            >
              Remove
            </Button>
          ) : (
            <Button
              ref={allowButton}
              size="small"
              variant="primary"
              pending={pending}
              onClick={() => onAllow(grant)}
            >
              Allow
            </Button>
          )}
        </div>
      )}
    </li>
  );
}
