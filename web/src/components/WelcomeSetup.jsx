import { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { X } from '@phosphor-icons/react';
import { useEngine } from '../store.jsx';
import { isAndroid, isIOS, isStandalone } from '../pwa.js';
import { dismissWelcome, welcomeDismissed } from '../phone-setup.js';
import { api } from '../api.js';
import AddToHomeScreen from './AddToHomeScreen.jsx';
import PhoneHandoff from './PhoneHandoff.jsx';
import Dialog from './ui/Dialog.jsx';
import Button from './ui/Button.jsx';

export default function WelcomeSetup() {
  const location = useLocation();
  const navigate = useNavigate();
  const requested = new URLSearchParams(location.search).get('setup') === 'phone';
  const [open, setOpen] = useState(
    () => requested || (location.pathname === '/' && !isStandalone() && !welcomeDismissed()),
  );
  const headingRef = useRef(null);
  const [pending, setPending] = useState(false);
  const { engine } = useEngine();
  const phone = isIOS() || isAndroid();

  // Two names, asked once. Neither is required — Vela works unnamed, and an
  // empty field is a perfectly good answer — so they are saved on the way out
  // rather than gating the dialog behind a form.
  const [names, setNames] = useState({ displayName: '', serverName: '' });
  const [asked, setAsked] = useState(false);
  useEffect(() => {
    let cancelled = false;
    api
      .getSettings()
      .then((data) => {
        if (cancelled) return;
        const identity = data?.identity || {};
        setNames({
          displayName: identity.displayName || '',
          serverName: identity.serverName || '',
        });
        // Someone who already named this server is not asked again.
        setAsked(Boolean(identity.displayName || identity.serverName));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  function close() {
    if (names.displayName.trim() || names.serverName.trim()) {
      api
        .updateSettings({ identity: names })
        .then(() => dispatchEvent(new Event('vela:identity-changed')))
        .catch(() => {});
    }
    dismissWelcome();
    setOpen(false);
    if (requested) {
      const search = new URLSearchParams(location.search);
      search.delete('setup');
      navigate(
        { pathname: location.pathname, search: search.toString(), hash: location.hash },
        { replace: true },
      );
    }
  }

  if (!engine || !open) return null;
  return (
    <Dialog
      open={open}
      pending={pending}
      onClose={close}
      className="modal-dialog welcome-dialog"
      initialFocusRef={headingRef}
      aria-labelledby="welcome-title"
      aria-describedby="welcome-description"
    >
      <header className="welcome-top">
        <div className="welcome-brand">
          <img src="/vela-mark.png" alt="" width="28" height="28" /> Vela
        </div>
        <Button
          variant="ghost"
          disabled={pending}
          className="welcome-close"
          aria-label="Close setup"
          onClick={close}
        >
          <X size={20} />
        </Button>
      </header>
      <div className="welcome-body">
        <p className="welcome-eyebrow">WELCOME TO VELA</p>
        <h2 id="welcome-title" tabIndex={-1} ref={headingRef}>
          {phone ? 'Vela, one tap away.' : 'Vela on your phone.'}
        </h2>
        <p id="welcome-description" className="welcome-description">
          {phone
            ? 'Follow the steps for this device to add Vela to your Home Screen.'
            : 'Scan the QR code with your iPhone or Android to open Vela and set up your Home Screen app.'}
        </p>
        {!asked && (
          <div className="welcome-names">
            <label className="field-label" htmlFor="welcome-display">
              What should Vela call you?
              <input
                id="welcome-display"
                type="text"
                className="field"
                maxLength={60}
                placeholder="Marco"
                value={names.displayName}
                onChange={(event) =>
                  setNames((current) => ({ ...current, displayName: event.target.value }))
                }
              />
            </label>
            <label className="field-label" htmlFor="welcome-server">
              And this computer?
              <input
                id="welcome-server"
                type="text"
                className="field"
                maxLength={60}
                placeholder="vela.marco.house"
                value={names.serverName}
                onChange={(event) =>
                  setNames((current) => ({ ...current, serverName: event.target.value }))
                }
              />
            </label>
            <p className="panel-note">
              Both are labels, and both can be left blank or changed later in Settings. The server
              name is not the address this computer answers on.
            </p>
          </div>
        )}

        {phone ? (
          <AddToHomeScreen appName="Vela" forHub />
        ) : (
          <PhoneHandoff onPendingChange={setPending} />
        )}
      </div>
      <footer className="welcome-footer">
        <Button variant="ghost" disabled={pending} onClick={close}>
          {phone ? 'Continue to Vela' : 'Done for now'}
        </Button>
        <p>You can reopen setup any time in Settings.</p>
      </footer>
    </Dialog>
  );
}
