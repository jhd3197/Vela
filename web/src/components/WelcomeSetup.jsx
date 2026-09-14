import { useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { ArrowRight, CheckCircle, Desktop, DeviceMobile, X } from '@phosphor-icons/react';
import { QRCodeSVG } from 'qrcode.react';
import { useAuth } from './AuthGate.jsx';
import { useEngine } from '../store.jsx';
import { isIOS, isStandalone } from '../pwa.js';
import { dismissWelcome, phoneSetupUrl, welcomeDismissed } from '../phone-setup.js';
import IOSInstallSteps from './IOSInstallSteps.jsx';
import Dialog from './ui/Dialog.jsx';
import Button from './ui/Button.jsx';

export default function WelcomeSetup() {
  const location = useLocation();
  const navigate = useNavigate();
  const requested = new URLSearchParams(location.search).get('setup') === 'phone';
  const [open, setOpen] = useState(
    () => requested || (location.pathname === '/' && !isStandalone() && !welcomeDismissed()),
  );
  const [step, setStep] = useState(requested || isIOS() ? 'phone' : 'welcome');
  const [copied, setCopied] = useState('');
  const headingRef = useRef(null);
  const { remote } = useAuth();
  const { engine } = useEngine();
  const url = phoneSetupUrl(window.location.origin, remote);
  const ios = isIOS();
  const standalone = isStandalone();

  function close() {
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

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied('Link copied.');
    } catch {
      setCopied('Select and copy the address above.');
    }
  }

  function changeStep(next) {
    setStep(next);
    headingRef.current?.focus();
  }

  if (!engine) return null;
  const title =
    step === 'welcome'
      ? 'Welcome to Vela.'
      : standalone
        ? 'Vela is on your Home Screen.'
        : ios
          ? 'Make room for Vela.'
          : 'Take Vela to your iPhone.';

  return (
    <Dialog
      open={open}
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
        <Button variant="ghost" className="welcome-close" aria-label="Close setup" onClick={close}>
          <X size={20} />
        </Button>
      </header>
      <div className="welcome-body">
        <p className="welcome-eyebrow">
          {step === 'welcome' ? 'YOUR PERSONAL APP SERVER' : 'VELA ON YOUR HOME SCREEN'}
        </p>
        <h2 id="welcome-title" tabIndex={-1} ref={headingRef}>
          {title}
        </h2>
        <p id="welcome-description" className="welcome-description">
          {step === 'welcome'
            ? 'Your apps live on this computer. Open them here, or bring them along on your phone.'
            : standalone
              ? 'Open Vela from its icon whenever you need your apps.'
              : ios
                ? 'Add your dashboard to the Home Screen for a full-screen app experience.'
                : 'Scan, open in Safari, and save it to your Home Screen.'}
        </p>

        {step === 'welcome' ? (
          <div className="welcome-devices" aria-hidden="true">
            <div>
              <Desktop size={46} weight="light" />
              <span>Your computer</span>
            </div>
            <span className="welcome-device-line" />
            <div>
              <DeviceMobile size={42} weight="light" />
              <span>Your iPhone</span>
            </div>
          </div>
        ) : standalone ? (
          <CheckCircle className="welcome-success" size={48} />
        ) : ios ? (
          <IOSInstallSteps />
        ) : url ? (
          <div className="welcome-handoff">
            <div className="welcome-qr">
              <QRCodeSVG
                value={url}
                size={200}
                marginSize={4}
                level="M"
                title="Scan to set up Vela on your iPhone"
              />
            </div>
            <div>
              <h3>Open your iPhone camera</h3>
              <p>
                Point it at this code and tap the link. Your phone will guide you through the next
                steps.
              </p>
              <p className="phone-note">
                Use the same Wi-Fi as your Vela computer, or a network that can reach it.
              </p>
              <label className="phone-link-label" htmlFor="phone-setup-link">
                Vela setup address
              </label>
              <input
                id="phone-setup-link"
                className="phone-link"
                readOnly
                value={url}
                onFocus={(event) => event.target.select()}
              />
              <Button onClick={copyLink}>Copy link</Button>
              <p className="phone-note" role="status">
                {copied}
              </p>
            </div>
          </div>
        ) : (
          <div className="welcome-network">
            <h3>First, connect your phone to Vela</h3>
            <p>
              This server is available on this computer only. Phone access needs a Vela password and
              a trusted HTTPS address.
            </p>
            <ol>
              <li>Follow the server guide to enable access from another device.</li>
              <li>Open the configured HTTPS address on this computer and sign in.</li>
              <li>
                Return to <strong>Settings → Set up my iPhone</strong> for your QR code.
              </li>
            </ol>
            <a
              className="btn"
              href="https://github.com/jhd3197/Vela/blob/dev/docs/SERVER.md#another-device"
              target="_blank"
              rel="noreferrer"
            >
              Open phone access guide <ArrowRight size={16} />
            </a>
          </div>
        )}
      </div>
      <footer className="welcome-footer">
        {step === 'welcome' ? (
          <>
            <Button variant="ghost" onClick={close}>
              Stay on this computer
            </Button>
            <Button variant="primary" onClick={() => changeStep('phone')}>
              Set up my iPhone <ArrowRight size={16} />
            </Button>
          </>
        ) : (
          <>
            {!ios && !standalone && (
              <Button variant="ghost" onClick={() => changeStep('welcome')}>
                Back
              </Button>
            )}
            <Button variant="primary" onClick={close}>
              {ios || standalone ? 'Continue to Vela' : 'Done for now'}
            </Button>
          </>
        )}
        <p>You can reopen setup any time in Settings.</p>
      </footer>
    </Dialog>
  );
}
