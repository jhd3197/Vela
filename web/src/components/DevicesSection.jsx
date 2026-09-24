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
//
// A TV has no camera, so it pairs the other way: it shows a code, and the code
// is entered here. The check is the start of this computer's certificate
// fingerprint; the TV works it out from the certificate it was given, so the two
// only match when nothing sat between them.
export default function DevicesSection() {
  const confirm = useConfirm();
  const { data, error, refresh } = useResource(api.getDevices);
  const [pairing, setPairing] = useState(null);
  const [tv, setTv] = useState(null);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState('');

  async function run(action) {
    setPending(true);
    setFailure('');
    try {
      await action();
    } catch (reason) {
      setFailure(reason.message);
    } finally {
      setPending(false);
    }
  }

  const startPairing = () => run(async () => setPairing(await api.startDevicePairing()));

  const lookUp = (event) => {
    event.preventDefault();
    run(async () => setTv({ ...tv, found: await api.lookupDeviceRequest(tv.code.trim()) }));
  };

  const approve = () =>
    run(async () => {
      await api.approveDeviceRequest(tv.code.trim());
      setTv(null);
      refresh();
    });

  async function remove(device) {
    const sure = await confirm({
      title: `Remove ${device.name}?`,
      message: 'It is signed out now and needs a new code to connect again.',
      confirmText: 'Remove',
    });
    if (!sure) return;
    run(async () => {
      await api.removeDevice(device.id);
      refresh();
    });
  }

  function done() {
    setPairing(null);
    refresh();
  }

  const devices = data?.devices || [];
  const idle = !pairing && !tv;
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
        {idle && (
          <div className="settings-devices-actions">
            <Button pending={pending} disabled={data && !data.available} onClick={startPairing}>
              Pair the Vela app
            </Button>
            <Button
              variant="ghost"
              disabled={data && !data.available}
              onClick={() => setTv({ code: '' })}
            >
              Add a TV
            </Button>
          </div>
        )}
      </div>
      {data && !data.available && idle && (
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
      {tv && !tv.found && (
        <form className="settings-devices-tv" onSubmit={lookUp}>
          <p>
            Open the Vela app on the TV and enter this computer’s address:{' '}
            <code className="mono">{data?.address}</code>. Then type the code the TV shows.
          </p>
          <label className="phone-link-label" htmlFor="tv-code">
            Code on the TV
          </label>
          <input
            id="tv-code"
            className="phone-link mono"
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            maxLength={12}
            placeholder="K7QM-3XPD"
            required
            value={tv.code}
            onChange={(event) => setTv({ code: event.target.value })}
          />
          <div className="form-actions">
            <Button variant="primary" type="submit" pending={pending}>
              Continue
            </Button>
            <Button variant="ghost" onClick={() => setTv(null)}>
              Cancel
            </Button>
          </div>
        </form>
      )}
      {tv?.found && (
        <div className="settings-devices-tv">
          <h3>Add {tv.found.name}?</h3>
          {tv.found.check ? (
            <p>
              The TV must show the check <code className="mono">{tv.found.check}</code>. If it shows
              anything else, cancel: the TV is not talking to this computer.
            </p>
          ) : (
            <p>It connects to {data?.address} as soon as you add it.</p>
          )}
          <div className="form-actions">
            <Button variant="primary" pending={pending} onClick={approve}>
              {tv.found.check ? 'They match, add the TV' : 'Add the TV'}
            </Button>
            <Button variant="ghost" onClick={() => setTv(null)}>
              Cancel
            </Button>
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
