import { useEffect, useRef, useState } from 'react';
import { CaretRight, Lock } from '@phosphor-icons/react';
import { api } from '../../api.js';
import Button from '../ui/Button.jsx';
import { useAuth } from '../AuthGate.jsx';
import { useSecurity } from '../SecurityProvider.jsx';
import PinPad, { PIN_LENGTH } from './PinPad.jsx';
import PatternPad from './PatternPad.jsx';
import { MIN_DOTS, samePattern } from '../../pattern.js';
import { setShowTrail, useShowTrail } from './trailPreference.js';

const TIMEOUTS = [
  { value: 60, label: '1 minute' },
  { value: 300, label: '5 minutes' },
  { value: 900, label: '15 minutes' },
];

const methodName = (method) => (method === 'pattern' ? 'Pattern' : 'PIN');
// "your PIN", "your pattern": the abbreviation keeps its capitals in a sentence.
const methodWord = (method) => (method === 'pattern' ? 'pattern' : 'PIN');
const timeoutLabel = (value) => TIMEOUTS.find((item) => item.value === value)?.label || '5 minutes';

function Row({ title, description, value, onClick, action, children }) {
  const body = (
    <>
      <span className="security-row-text">
        <span className="security-row-title">{title}</span>
        {description && <span className="security-row-description">{description}</span>}
      </span>
      {value && <span className="security-row-value">{value}</span>}
      {onClick && <CaretRight size={16} aria-hidden="true" />}
      {action}
      {children}
    </>
  );
  return onClick ? (
    <button type="button" className="security-row security-row-button" onClick={onClick}>
      {body}
    </button>
  ) : (
    <div className="security-row">{body}</div>
  );
}

// App lock: what it protects, how it is set up, and how it is turned off. Every
// change here verifies the Vela password again, and the engine — not this
// component — decides whether a session is locked.
export default function SecuritySection({ onPendingChange, onSubScreen }) {
  const { status, apply, refresh } = useSecurity();
  const { remote, logout } = useAuth();
  const [flow, setFlow] = useState(null);
  const [password, setPassword] = useState('');
  const [method, setMethod] = useState('pin');
  const [timeout_, setTimeout_] = useState(300);
  const [pin, setPin] = useState('');
  const [dots, setDots] = useState([]);
  const [firstSecret, setFirstSecret] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const submitting = useRef(false);
  const headingRef = useRef(null);
  const showTrail = useShowTrail();

  const enrolled = Boolean(status?.enrolled);
  const available = Boolean(status?.available);

  useEffect(() => {
    onPendingChange?.('security', busy);
    return () => onPendingChange?.('security', false);
  }, [busy, onPendingChange]);

  const reset = () => {
    setFlow(null);
    setPassword('');
    setPin('');
    setDots([]);
    setFirstSecret(null);
    setError('');
  };

  useEffect(() => {
    onSubScreen?.(flow ? reset : null);
    return () => onSubScreen?.(null);
  }, [flow, onSubScreen]);

  useEffect(() => {
    if (flow) headingRef.current?.focus();
  }, [flow]);

  const run = async (work, done, onFailure) => {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError('');
    try {
      apply(await work());
      done?.();
    } catch (failure) {
      setError(
        failure.status === 0
          ? 'Cannot reach Vela right now. Nothing was changed.'
          : failure.message,
      );
      onFailure?.(failure);
      refresh();
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  };

  // ------------------------------------------------------------------ screens

  if (!available) {
    return (
      <section className="panel security-panel">
        <div className="panel-head">
          <h2>App lock</h2>
        </div>
        <p className="panel-note">
          App lock protects a phone or remote browser session that signs in with your Vela password.
          You are on the Vela computer itself, where the dashboard opens without signing in, so
          there is no session here to lock. Use your computer’s own screen lock for this machine.
        </p>
      </section>
    );
  }

  if (flow === 'password') {
    const changing = enrolled;
    return (
      <section className="panel security-panel">
        <div className="panel-head">
          <h2 tabIndex={-1} ref={headingRef}>
            {changing ? `Change your ${methodWord(status.method)}` : 'Set up app lock'}
          </h2>
        </div>
        <p className="panel-note">
          Confirm your Vela password first. Your quick unlock is only for this signed-in session on
          this device.
        </p>
        <form
          className="security-form"
          onSubmit={(event) => {
            event.preventDefault();
            setError('');
            setFlow('enter');
          }}
        >
          <label htmlFor="security-password">Vela password</label>
          <input
            id="security-password"
            type="password"
            autoComplete="current-password"
            maxLength={256}
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          <fieldset className="security-methods">
            <legend>Unlock with</legend>
            {['pin', 'pattern'].map((option) => (
              <label key={option} className="security-method">
                <input
                  type="radio"
                  name="security-method"
                  value={option}
                  checked={method === option}
                  onChange={() => setMethod(option)}
                />
                <span>
                  <strong>
                    {methodName(option)}
                    {option === 'pin' ? ' (recommended)' : ''}
                  </strong>
                  <small>
                    {option === 'pin'
                      ? 'Six digits, entered on a keypad.'
                      : 'Connect at least four of nine dots.'}
                  </small>
                </span>
              </label>
            ))}
          </fieldset>
          {error && (
            <p className="inline-error" role="alert">
              {error}
            </p>
          )}
          <div className="actions">
            <Button variant="primary" type="submit" disabled={!password}>
              Continue
            </Button>
            <Button onClick={reset}>Cancel</Button>
          </div>
        </form>
      </section>
    );
  }

  if (flow === 'enter' || flow === 'confirm') {
    const confirming = flow === 'confirm';
    const ready = method === 'pin' ? pin.length === PIN_LENGTH : dots.length >= MIN_DOTS;
    const submit = () => {
      const secret = method === 'pin' ? pin : dots;
      if (!confirming) {
        setFirstSecret(secret);
        setPin('');
        setDots([]);
        setError('');
        setFlow('confirm');
        return;
      }
      const matches = method === 'pin' ? firstSecret === pin : samePattern(firstSecret, dots);
      if (!matches) {
        setPin('');
        setDots([]);
        setError(
          method === 'pin'
            ? 'Those PINs are different. Enter the new PIN again.'
            : 'Those patterns are different. Draw the new pattern again.',
        );
        setFlow('enter');
        setFirstSecret(null);
        return;
      }
      run(
        () => api.enrollSecurity({ password, method, secret }),
        () => {
          setNote(`App lock is on. You will unlock Vela with your ${methodWord(method)}.`);
          reset();
        },
        // A refused password belongs on the screen that asked for it, rather
        // than at the end of a journey the reader would then have to repeat.
        (failure) => {
          if (failure.status === 401) {
            setPin('');
            setDots([]);
            setFirstSecret(null);
            setFlow('password');
          }
        },
      );
    };
    return (
      <section className="panel security-panel">
        <div className="panel-head">
          <h2 tabIndex={-1} ref={headingRef}>
            {confirming
              ? method === 'pin'
                ? 'Repeat your new PIN'
                : 'Draw your pattern again'
              : method === 'pin'
                ? 'Choose a six-digit PIN'
                : 'Draw an unlock pattern'}
          </h2>
        </div>
        {method === 'pin' ? (
          <PinPad
            autoFocus
            value={pin}
            onChange={setPin}
            disabled={busy}
            label={confirming ? 'Repeat PIN' : 'New PIN'}
          />
        ) : (
          <PatternPad
            value={dots}
            onChange={setDots}
            disabled={busy}
            showTrail={showTrail}
            label={confirming ? 'Repeat pattern' : 'New pattern'}
          />
        )}
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        <div className="actions">
          <Button variant="primary" disabled={!ready || busy} onClick={submit}>
            {busy ? 'Saving…' : confirming ? 'Turn on app lock' : 'Continue'}
          </Button>
          <Button disabled={busy} onClick={reset}>
            Cancel
          </Button>
        </div>
      </section>
    );
  }

  if (flow === 'timeout') {
    return (
      <section className="panel security-panel">
        <div className="panel-head">
          <h2 tabIndex={-1} ref={headingRef}>
            Lock after inactivity
          </h2>
        </div>
        <form
          className="security-form"
          onSubmit={(event) => {
            event.preventDefault();
            run(
              () => api.setSecurityTimeout({ password, timeout: timeout_ }),
              () => {
                setNote(`Vela will lock after ${timeoutLabel(timeout_)} without activity.`);
                reset();
              },
            );
          }}
        >
          <fieldset className="security-methods">
            <legend>Lock this session after</legend>
            {TIMEOUTS.map((option) => (
              <label key={option.value} className="security-method">
                <input
                  type="radio"
                  name="security-timeout"
                  checked={timeout_ === option.value}
                  onChange={() => setTimeout_(option.value)}
                />
                <span>
                  <strong>{option.label}</strong>
                </span>
              </label>
            ))}
          </fieldset>
          <label htmlFor="security-timeout-password">Vela password</label>
          <input
            id="security-timeout-password"
            type="password"
            autoComplete="current-password"
            maxLength={256}
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          {error && (
            <p className="inline-error" role="alert">
              {error}
            </p>
          )}
          <div className="actions">
            <Button variant="primary" type="submit" disabled={busy || !password}>
              {busy ? 'Saving…' : 'Save'}
            </Button>
            <Button disabled={busy} onClick={reset}>
              Cancel
            </Button>
          </div>
        </form>
      </section>
    );
  }

  if (flow === 'disable') {
    return (
      <section className="panel security-panel">
        <div className="panel-head">
          <h2 tabIndex={-1} ref={headingRef}>
            Turn off app lock
          </h2>
        </div>
        <p className="panel-note">
          This session will stay open until you sign out or it expires. You can turn app lock on
          again at any time.
        </p>
        <form
          className="security-form"
          onSubmit={(event) => {
            event.preventDefault();
            run(
              () => api.disableSecurity(password),
              () => {
                setNote('App lock is off.');
                reset();
              },
            );
          }}
        >
          <label htmlFor="security-disable-password">Vela password</label>
          <input
            id="security-disable-password"
            type="password"
            autoComplete="current-password"
            maxLength={256}
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          {error && (
            <p className="inline-error" role="alert">
              {error}
            </p>
          )}
          <div className="actions">
            <Button variant="primary" type="submit" disabled={busy || !password}>
              {busy ? 'Turning off…' : 'Turn off app lock'}
            </Button>
            <Button disabled={busy} onClick={reset}>
              Cancel
            </Button>
          </div>
        </form>
      </section>
    );
  }

  // --------------------------------------------------------------- home rows

  return (
    <section className="panel security-panel">
      <div className="panel-head">
        <h2>App lock</h2>
      </div>
      <p className="panel-note">
        A quick unlock for this signed-in session on this device, so a glance at your phone does not
        show your apps. It does not encrypt files on your Vela computer or lock that computer’s
        screen.
      </p>
      {note && (
        <p className="panel-note security-note" role="status">
          {note}
        </p>
      )}
      <div className="security-rows">
        <Row
          title="App lock"
          description={
            enrolled
              ? `Unlock with your ${methodWord(status.method)}.`
              : 'Off — this session opens straight into Vela.'
          }
          value={enrolled ? 'On' : 'Off'}
          onClick={() => {
            setMethod(status?.method || 'pin');
            setTimeout_(status?.timeout || 300);
            setPassword('');
            setNote('');
            setFlow('password');
          }}
        />
        {enrolled && (
          <>
            <Row
              title="Unlock method"
              description={`Change your ${methodWord(status.method)}, or switch method.`}
              value={methodName(status.method)}
              onClick={() => {
                setMethod(status.method);
                setPassword('');
                setNote('');
                setFlow('password');
              }}
            />
            <Row
              title="Lock after inactivity"
              description="Background updates do not count as activity."
              value={timeoutLabel(status.timeout)}
              onClick={() => {
                setTimeout_(status.timeout);
                setPassword('');
                setNote('');
                setFlow('timeout');
              }}
            />
            {status.method === 'pattern' && (
              <Row
                title="Show pattern trail"
                description="Draw a visible line between the dots you connect."
                action={
                  <div className="seg" role="group" aria-label="Show pattern trail">
                    {[true, false].map((value) => (
                      <button
                        key={String(value)}
                        type="button"
                        aria-pressed={showTrail === value}
                        className={`seg-opt${showTrail === value ? ' seg-opt-active' : ''}`}
                        onClick={() => setShowTrail(value)}
                      >
                        {value ? 'On' : 'Off'}
                      </button>
                    ))}
                  </div>
                }
              />
            )}
          </>
        )}
      </div>
      <div className="actions">
        {enrolled && (
          <>
            <Button disabled={busy} onClick={() => run(() => api.lockNow())}>
              <Lock size={16} aria-hidden="true" /> Lock now
            </Button>
            <Button
              disabled={busy}
              onClick={() => {
                setPassword('');
                setNote('');
                setFlow('disable');
              }}
            >
              Turn off app lock
            </Button>
          </>
        )}
        {remote && <Button onClick={logout}>Sign out</Button>}
      </div>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
