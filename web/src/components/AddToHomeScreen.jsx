import { useState } from 'react';
import { isIOS, isStandalone, promptInstall, useInstallPrompt } from '../pwa.js';
import IOSInstallSteps from './IOSInstallSteps.jsx';

// "Add to Home Screen" section. Used for web apps in the detail drawer and
// (with forHub) for the hub itself. iOS has no install-prompt API on any
// browser, so it always gets manual steps; Chrome/Edge/Android get a button
// wired to the captured beforeinstallprompt event.
export default function AddToHomeScreen({ appName, forHub = false }) {
  const installEvent = useInstallPrompt();
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState(null);

  const target = forHub ? 'the Vela hub' : appName;

  if (isStandalone()) {
    return (
      <section className="drawer-section a2hs">
        <h3 className="drawer-section-title">Add to Home Screen</h3>
        <p className="a2hs-note">
          Already installed — you are running {target} from the home screen.
        </p>
      </section>
    );
  }

  if (isIOS()) {
    return (
      <section className="drawer-section a2hs">
        <h3 className="drawer-section-title">Add to Home Screen</h3>
        <IOSInstallSteps />
        {!forHub && (
          <p className="a2hs-note">The same steps install the Vela hub itself from this page.</p>
        )}
      </section>
    );
  }

  if (installEvent) {
    const onClick = async () => {
      setBusy(true);
      try {
        setOutcome(await promptInstall(installEvent));
      } finally {
        setBusy(false);
      }
    };
    return (
      <section className="drawer-section a2hs">
        <h3 className="drawer-section-title">Add to Home Screen</h3>
        <p className="a2hs-note">Install {target} for a full-screen, app-like experience.</p>
        <button className="btn btn-primary" onClick={onClick} disabled={busy}>
          Install app
        </button>
        {outcome === 'dismissed' && (
          <p className="a2hs-note">Install dismissed — you can install any time from this panel.</p>
        )}
        {!forHub && (
          <p className="a2hs-note">The same flow installs the Vela hub itself from this page.</p>
        )}
      </section>
    );
  }

  return (
    <section className="drawer-section a2hs">
      <h3 className="drawer-section-title">Add to Home Screen</h3>
      <p className="a2hs-note">
        Open your browser&apos;s menu and choose <strong>Add to Home Screen</strong> or{' '}
        <strong>Install app</strong> to keep {target} on your home screen.
      </p>
      {!forHub && <p className="a2hs-note">The same applies to the Vela hub itself.</p>}
    </section>
  );
}
