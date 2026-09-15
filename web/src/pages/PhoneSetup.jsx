import { Link } from 'react-router-dom';
import { useEffect, useState } from 'react';
import AddToHomeScreen from '../components/AddToHomeScreen.jsx';
import { dismissWelcome } from '../phone-setup.js';
import { isIOS } from '../pwa.js';
import IOSInstallSteps from '../components/IOSInstallSteps.jsx';
import PhoneCertificateSteps from '../components/PhoneCertificateSteps.jsx';
import Button from '../components/ui/Button.jsx';

// Public instructions contain no server data or credentials. Scanning a QR
// should explain Safari before making someone sign in to the wrong browser.
export default function PhoneSetup() {
  const [connection, setConnection] = useState(null);
  const [loading, setLoading] = useState(window.location.protocol === 'http:');
  const [failed, setFailed] = useState(false);
  const needsCertificate =
    window.location.protocol === 'http:' &&
    !['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname);
  useEffect(() => {
    if (window.location.protocol !== 'http:') return;
    let cancelled = false;
    fetch('/phone-bootstrap', { cache: 'no-store' })
      .then(async (response) => {
        if (!response.ok || !response.headers.get('content-type')?.includes('application/json'))
          throw new Error('The Wi-Fi setup service did not respond');
        const data = await response.json();
        const target = new URL(data.secure_url);
        if (
          target.protocol !== 'https:' ||
          target.hostname !== window.location.hostname ||
          target.username ||
          target.password ||
          target.pathname !== '/setup' ||
          target.search ||
          target.hash ||
          data.certificate_url !== '/vela-phone.cer'
        )
          throw new Error('The secure setup address is invalid');
        if (!cancelled) setConnection(data);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return (
    <main className="phone-setup-page">
      <article className="phone-setup-card">
        <div className="welcome-brand">
          <img src="/vela-mark.png" alt="" width="32" height="32" /> Vela
        </div>
        <p className="welcome-eyebrow">YOUR APPS, ONE TAP AWAY</p>
        <h1>Bring Vela home.</h1>
        <p className="welcome-description">
          Save your dashboard to your Home Screen. Your apps and data stay on your Vela computer.
        </p>
        {loading ? (
          <p role="status">Checking your connection…</p>
        ) : failed && needsCertificate ? (
          <div className="phone-instructions">
            <p role="alert">
              Could not load the Wi-Fi connection steps. Keep your Vela computer on and check that
              both devices are on the same Wi-Fi.
            </p>
            <Button onClick={() => window.location.reload()}>Retry connection</Button>
          </div>
        ) : connection ? (
          isIOS() ? (
            <IOSInstallSteps>
              <PhoneCertificateSteps connection={connection} />
            </IOSInstallSteps>
          ) : (
            <PhoneCertificateSteps connection={connection} />
          )
        ) : (
          <AddToHomeScreen appName="Vela" forHub />
        )}
        {!loading && !connection && !(failed && needsCertificate) && (
          <>
            <Link className="btn btn-primary" to="/" onClick={dismissWelcome}>
              Continue to Vela
            </Link>
            <p className="phone-note">Sign in with your Vela password when asked.</p>
          </>
        )}
      </article>
    </main>
  );
}
