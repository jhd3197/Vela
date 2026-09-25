import { useState } from 'react';
import { api } from '../api.js';
import { useApps } from '../store.jsx';
import { useResource } from '../hooks/useResource.js';
import Button from './ui/Button.jsx';
import Dialog from './ui/Dialog.jsx';
import AppIcon from './AppIcon.jsx';
import { CompanionFacts } from './CompanionAppView.jsx';

// Desktop apps running on this computer that registered with Vela and have not
// been connected. Nothing is shown or called until the owner connects one, and
// connecting sends back the fingerprint of exactly what was on screen.
export default function FoundCompanions() {
  const { refreshApps, pushToast } = useApps();
  const { data, refresh } = useResource(api.getCompanions, { intervalMs: 5000 });
  const [reviewing, setReviewing] = useState(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const found = data?.found || [];
  if (!found.length && !reviewing) return null;

  const connect = async () => {
    setPending(true);
    setError('');
    try {
      await api.connectCompanion(reviewing.id, reviewing.fingerprint);
      refreshApps();
      refresh();
      pushToast?.(`${reviewing.name} connected`);
      setReviewing(null);
    } catch (failure) {
      setError(failure.message);
      refresh();
    } finally {
      setPending(false);
    }
  };

  return (
    <section className="companions-found" aria-label="Found on this computer">
      <h2 className="section-title">Found on this computer</h2>
      <ul className="group-list">
        {found.map((entry) => (
          <li key={entry.id} className="companions-found-row">
            <AppIcon
              app={{ id: `pc--${entry.id}`, name: entry.name, color: entry.color }}
              size={36}
            />
            <span className="companions-found-text">
              <span>{entry.name}</span>
              <small>{entry.description || 'Running now'}</small>
            </span>
            <Button size="small" variant="primary" onClick={() => setReviewing(entry)}>
              Connect
            </Button>
          </li>
        ))}
      </ul>
      {reviewing ? (
        <Dialog
          open
          onClose={() => setReviewing(null)}
          pending={pending}
          className="modal-dialog connected-app-dialog"
          aria-labelledby="companion-connect-title"
        >
          <h2 id="companion-connect-title">Connect {reviewing.name}?</h2>
          <p>It shows on your desk and phones, and its buttons run on this computer.</p>
          <CompanionFacts entry={reviewing} />
          {error ? <p role="alert">{error}</p> : null}
          <div className="connected-app-actions">
            <Button onClick={() => setReviewing(null)} disabled={pending}>
              Cancel
            </Button>
            <Button variant="primary" pending={pending} onClick={connect}>
              Connect
            </Button>
          </div>
        </Dialog>
      ) : null}
    </section>
  );
}
