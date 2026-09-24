import { useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { Trash } from '@phosphor-icons/react';
import { api, relTime } from '../api.js';
import { useResource } from '../hooks/useResource.js';
import { useConfirm } from '../hooks/useConfirm.js';
import Button from './ui/Button.jsx';

const FORM_LABEL = { phone: 'Phone', tablet: 'Tablet', tv: 'TV' };

// The Vela app for Android pairs by scanning a one-time code, and keeps its own
// credential instead of the password. Each paired device can be removed here on
// its own; removing one signs it out at once.
export default function DevicesSection() {
  const confirm = useConfirm();
  const { data, error, refresh } = useResource(api.getDevices);
  const [pairing, setPairing] = useState(null);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState('');

  async function startPairing() {
    setPending(true);
    setFailure('');
    try {
      setPairing(await api.startDevicePairing());
    } catch (reason) {
      setFailure(reason.message);
    } finally {
      setPending(false);
    }
  }

  async function remove(device) {
    const sure = await confirm({
      title: `Remove ${device.name}?`,
      message: 'It is signed out now and needs a new code to connect again.',
      confirmText: 'Remove',
    });
    if (!sure) return;
    setFailure('');
    try {
      await api.removeDevice(device.id);
      refresh();
    } catch (reason) {
      setFailure(reason.message);
    }
  }

  function done() {
    setPairing(null);
    refresh();
  }

  const devices = data?.devices || [];
  return (
    <div className="settings-devices">
      <div className="settings-row">
        <div>
          <h3>Vela app</h3>
          <p>
            Pair the Vela app for Android. It connects without a certificate setup or your password,
            and you can remove it here at any time.
          </p>
        </div>
        {!pairing && (
          <Button pending={pending} disabled={data && !data.available} onClick={startPairing}>
            Pair the Vela app
          </Button>
        )}
      </div>
      {data && !data.available && !pairing && (
        <p className="phone-note">Turn on Wi-Fi access with Set up my phone first.</p>
      )}
      {pairing && (
        <div className="welcome-handoff">
          <div className="welcome-qr">
            <QRCodeSVG
              value={pairing.link}
              size={200}
              marginSize={4}
              level="M"
              title="Scan with the Vela app"
            />
          </div>
          <div>
            <h3>Scan with the Vela app</h3>
            <p>
              Open the Vela app on your Android device and tap Scan QR code. The code works once,
              for the next 10 minutes.
            </p>
            <p className="phone-note">
              Code <code className="mono">{pairing.code}</code>
            </p>
            <Button onClick={done}>Done</Button>
          </div>
        </div>
      )}
      {devices.length > 0 && (
        <ul className="settings-volume-list">
          {devices.map((device) => (
            <li key={device.id}>
              <span className="settings-volume-text">
                <span>{device.name}</span>
                <small>
                  {FORM_LABEL[device.form] || 'Device'} · last connected{' '}
                  {relTime(device.lastSeenAt)}
                </small>
              </span>
              <Button
                size="icon"
                variant="ghost"
                aria-label={`Remove ${device.name}`}
                onClick={() => remove(device)}
              >
                <Trash size={16} aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      )}
      {(failure || (error && !data)) && (
        <p className="phone-note" role="alert">
          {failure || error.message}
        </p>
      )}
    </div>
  );
}
