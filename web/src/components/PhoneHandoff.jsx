import { useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { api } from '../api.js';
import { useResource } from '../hooks/useResource.js';
import { phoneSetupUrl } from '../phone-setup.js';
import Button from './ui/Button.jsx';
import { copyText } from '../clipboard.js';

export default function PhoneHandoff({ onPendingChange }) {
  const { data, error, loading, refresh } = useResource(api.getPhoneAccess);
  const [updated, setUpdated] = useState(null);
  const [address, setAddress] = useState('');
  const [password, setPassword] = useState('');
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState('');
  const [copied, setCopied] = useState('');
  const access = updated || data;
  const url = access?.enabled
    ? phoneSetupUrl(access.setup_url, true, { allowLocalHttp: access.managed })
    : null;

  async function enable(event) {
    event.preventDefault();
    setPending(true);
    onPendingChange(true);
    setFailure('');
    try {
      setUpdated(
        await api.enablePhoneAccess({ address: address || access.addresses[0], password }),
      );
      setPassword('');
    } catch (error) {
      setFailure(error.message);
    } finally {
      setPending(false);
      onPendingChange(false);
    }
  }

  async function disable() {
    setPending(true);
    onPendingChange(true);
    setFailure('');
    try {
      setUpdated(await api.disablePhoneAccess());
    } catch (error) {
      setFailure(error.message);
    } finally {
      setPending(false);
      onPendingChange(false);
    }
  }

  async function copyLink() {
    setCopied((await copyText(url)) ? 'Link copied.' : 'Select and copy the address above.');
  }

  if (loading && !access)
    return (
      <p className="phone-note" role="status">
        Finding your phone connection…
      </p>
    );
  if (!access)
    return (
      <div className="welcome-network">
        <p role="alert">{error?.message || 'Could not load phone access.'}</p>
        <Button onClick={refresh}>Retry</Button>
      </div>
    );

  return (
    <>
      {url ? (
        <>
          <div className="welcome-handoff">
            <div className="welcome-qr">
              <QRCodeSVG
                value={url}
                size={200}
                marginSize={4}
                level="M"
                title="Scan to open Vela on your phone"
              />
            </div>
            <div>
              <h3>Scan with your phone’s camera</h3>
              <p>
                Tap the link to open Vela. The page will show the right setup steps for your iPhone
                or Android.
              </p>
              <p className="phone-note">
                {access.managed
                  ? 'Connect your phone to the same Wi-Fi as this computer.'
                  : 'Your phone must be able to reach this Vela address.'}
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
          {access.managed && (
            <details className="phone-connection-details">
              <summary>Wi-Fi connection details</summary>
              <p>
                The phone page includes a one-time certificate setup. Compare its certificate’s
                SHA-256 fingerprint with this computer before trusting it:
              </p>
              <code className="phone-fingerprint">{access.fingerprint}</code>
              <p>
                If scanning does not connect, check that both devices use the same Wi-Fi and allow
                Vela through this computer’s firewall on the private network. Guest Wi-Fi may block
                devices from connecting.
              </p>
              <Button disabled={pending} onClick={disable}>
                Turn off Wi-Fi access
              </Button>
              <p className="phone-note">
                This disconnects phones and keeps the dashboard running on this computer.
              </p>
            </details>
          )}
        </>
      ) : access.managed ? (
        <form className="welcome-network" onSubmit={enable}>
          <h3>Connect over your Wi-Fi</h3>
          <p>Enable phone access once to get your QR code. Your apps stay on this computer.</p>
          {access.addresses?.length > 0 ? (
            <>
              <label className="phone-link-label" htmlFor="wifi-address">
                This computer’s network address
              </label>
              <select
                id="wifi-address"
                className="phone-link"
                value={address || access.addresses[0]}
                onChange={(event) => setAddress(event.target.value)}
                disabled={pending}
              >
                {access.addresses.map((ip) => (
                  <option key={ip}>{ip}</option>
                ))}
              </select>
              {access.needs_password ? (
                <>
                  <label className="phone-link-label" htmlFor="wifi-password">
                    Choose a Vela password
                  </label>
                  <input
                    id="wifi-password"
                    className="phone-link"
                    type="password"
                    autoComplete="new-password"
                    minLength={12}
                    maxLength={256}
                    required
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    disabled={pending}
                    aria-describedby="wifi-password-help"
                  />
                  <p id="wifi-password-help" className="phone-note">
                    At least 12 characters. Use this password to sign in on your phone.
                  </p>
                </>
              ) : (
                <p className="phone-note">
                  Sign in on your phone with your existing Vela password.
                </p>
              )}
              <Button variant="primary" pending={pending} type="submit">
                {pending ? 'Starting Wi-Fi access…' : 'Enable Wi-Fi & show QR'}
              </Button>
              <p className="phone-note">
                Your phone will guide you through trusting this computer’s certificate, then adding
                Vela to your Home Screen.
              </p>
            </>
          ) : (
            <>
              <p>Connect this computer to Wi-Fi, then try again.</p>
              <Button onClick={refresh}>Check again</Button>
            </>
          )}
          {access.error && <p className="phone-note">{access.error}</p>}
        </form>
      ) : (
        <p role="alert">The configured server address cannot be used on a phone.</p>
      )}
      {failure && (
        <p className="phone-note" role="alert">
          {failure}
        </p>
      )}
    </>
  );
}
