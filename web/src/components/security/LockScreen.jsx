import { useEffect, useRef, useState } from 'react';
import { api } from '../../api.js';
import Dialog from '../ui/Dialog.jsx';
import PinPad from './PinPad.jsx';
import PatternPad from './PatternPad.jsx';
import { MIN_DOTS } from '../../pattern.js';
import { useShowTrail } from './trailPreference.js';

// The lock screen covers the whole dashboard. It is a modal dialog, so the
// browser makes everything behind it inert while it is open — but the real
// protection is the engine, which refuses protected requests for a locked
// session whatever this page does.
export default function LockScreen({ status, checking = false, onUnlocked }) {
  const [pin, setPin] = useState('');
  const [dots, setDots] = useState([]);
  const [password, setPassword] = useState('');
  const [usePassword, setUsePassword] = useState(Boolean(status?.passwordRequired));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const passwordRef = useRef(null);
  const submitting = useRef(false);
  const showTrail = useShowTrail();
  const method = status?.method;
  const forced = Boolean(status?.passwordRequired);

  useEffect(() => {
    if (forced) setUsePassword(true);
  }, [forced]);

  useEffect(() => {
    if (usePassword) passwordRef.current?.focus();
  }, [usePassword]);

  const attempt = async (body, reset) => {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError('');
    try {
      const next = await api.unlock(body);
      onUnlocked(next);
    } catch (failure) {
      setError(
        failure.status === 0
          ? 'Cannot reach Vela right now. Check your connection and try again.'
          : failure.status === 401 && failure.message.startsWith('Sign in')
            ? 'This session expired. Sign in with your Vela password.'
            : failure.message,
      );
      reset?.();
      api
        .getSecurity()
        .then(onUnlocked)
        .catch(() => {});
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  };

  if (checking) {
    return (
      <Dialog open pending className="modal-dialog lock-dialog" aria-labelledby="lock-title">
        <div className="lock-screen">
          <img src="/vela-mark.png" width="40" height="40" alt="" />
          <h1 id="lock-title">Checking Vela…</h1>
          <p role="status">Confirming this session is still unlocked.</p>
        </div>
      </Dialog>
    );
  }

  return (
    <Dialog open pending className="modal-dialog lock-dialog" aria-labelledby="lock-title">
      <div className="lock-screen">
        <img src="/vela-mark.png" width="40" height="40" alt="" />
        <h1 id="lock-title">Vela is locked</h1>
        <p>
          {usePassword
            ? forced
              ? 'Too many attempts. Enter your Vela password to continue.'
              : 'Enter your Vela password to unlock this session.'
            : method === 'pattern'
              ? 'Draw your unlock pattern to continue.'
              : 'Enter your PIN to continue.'}
        </p>

        {usePassword ? (
          <form
            className="lock-password"
            onSubmit={(event) => {
              event.preventDefault();
              attempt({ password }, () => setPassword(''));
            }}
          >
            <label htmlFor="lock-password-field">Vela password</label>
            <input
              id="lock-password-field"
              ref={passwordRef}
              type="password"
              autoComplete="current-password"
              maxLength={256}
              required
              disabled={busy}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            <button className="btn btn-primary" disabled={busy || !password}>
              {busy ? 'Unlocking…' : 'Unlock'}
            </button>
          </form>
        ) : method === 'pattern' ? (
          <PatternPad
            value={dots}
            onChange={setDots}
            disabled={busy}
            showTrail={showTrail}
            label="Unlock pattern"
            onComplete={(drawn) => {
              if (drawn.length < MIN_DOTS) {
                setDots([]);
                setError(`Connect at least ${MIN_DOTS} dots.`);
                return;
              }
              attempt({ secret: drawn }, () => setDots([]));
              setDots([]);
            }}
          />
        ) : (
          <PinPad
            autoFocus
            value={pin}
            onChange={setPin}
            disabled={busy}
            label="PIN"
            onComplete={(entered) => attempt({ secret: entered }, () => setPin(''))}
          />
        )}

        <p className="lock-status" role="status">
          {busy
            ? 'Checking…'
            : status?.attemptsRemaining != null && status.attemptsRemaining < 5 && !forced
              ? `${status.attemptsRemaining} attempt${status.attemptsRemaining === 1 ? '' : 's'} left before your password is required.`
              : ''}
        </p>
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}

        {!forced && (
          <button
            type="button"
            className="link-btn"
            onClick={() => {
              setUsePassword((on) => !on);
              setError('');
              setPin('');
              setDots([]);
            }}
          >
            {usePassword
              ? `Use my ${method === 'pattern' ? 'pattern' : 'PIN'}`
              : 'Use Vela password'}
          </button>
        )}
      </div>
    </Dialog>
  );
}
