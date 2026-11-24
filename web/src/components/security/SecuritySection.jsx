import { useEffect, useRef, useState } from 'react';
import { Lock } from '@phosphor-icons/react';
import { api } from '../../api.js';
import Button from '../ui/Button.jsx';
import {
  SettingRow,
  SettingsActions,
  SettingsGroup,
  SettingsPage,
  SettingsStatus,
} from '../settings/SettingsKit.jsx';
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

  // The password every change is confirmed with, as one settings row.
  const passwordRow = (id) => (
    <SettingRow
      title="Vela password"
      htmlFor={id}
      control={
        <input
          id={id}
          type="password"
          autoComplete="current-password"
          maxLength={256}
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      }
    />
  );

  if (!available) {
    return (
      <SettingsPage>
        <SettingsGroup
          title="App lock"
          description="App lock protects a phone or remote browser session that signs in with your Vela password."
        >
          <SettingRow
            title="App lock"
            description="You are on the Vela computer itself, where the dashboard opens without signing in, so there is no session here to lock. Use your computer’s own screen lock for this machine."
            value="Not needed here"
          />
        </SettingsGroup>
      </SettingsPage>
    );
  }

  if (flow === 'password') {
    const changing = enrolled;
    return (
      <form
        className="set-page"
        onSubmit={(event) => {
          event.preventDefault();
          setError('');
          setFlow('enter');
        }}
      >
        <SettingsGroup
          title={changing ? `Change your ${methodWord(status.method)}` : 'Set up app lock'}
          headingRef={headingRef}
          description="Confirm your Vela password first. Your quick unlock is only for this signed-in session on this device."
          footer={
            <>
              <SettingsStatus tone="error">{error}</SettingsStatus>
              <SettingsActions>
                <Button variant="primary" type="submit" disabled={!password}>
                  Continue
                </Button>
                <Button onClick={reset}>Cancel</Button>
              </SettingsActions>
            </>
          }
        >
          {passwordRow('security-password')}
          <SettingRow title="Unlock with" stacked>
            <fieldset className="security-methods">
              <legend className="sr-only">Unlock with</legend>
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
          </SettingRow>
        </SettingsGroup>
      </form>
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
      <SettingsPage>
        <SettingsGroup
          title={
            confirming
              ? method === 'pin'
                ? 'Repeat your new PIN'
                : 'Draw your pattern again'
              : method === 'pin'
                ? 'Choose a six-digit PIN'
                : 'Draw an unlock pattern'
          }
          headingRef={headingRef}
          footer={
            <>
              <SettingsStatus tone="error">{error}</SettingsStatus>
              <SettingsActions>
                <Button variant="primary" disabled={!ready || busy} onClick={submit}>
                  {busy ? 'Saving…' : confirming ? 'Turn on app lock' : 'Continue'}
                </Button>
                <Button disabled={busy} onClick={reset}>
                  Cancel
                </Button>
              </SettingsActions>
            </>
          }
        >
          <div className="security-pad">
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
          </div>
        </SettingsGroup>
      </SettingsPage>
    );
  }

  if (flow === 'timeout') {
    return (
      <form
        className="set-page"
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
        <SettingsGroup
          title="Lock after inactivity"
          headingRef={headingRef}
          footer={
            <>
              <SettingsStatus tone="error">{error}</SettingsStatus>
              <SettingsActions>
                <Button variant="primary" type="submit" disabled={busy || !password}>
                  {busy ? 'Saving…' : 'Save'}
                </Button>
                <Button disabled={busy} onClick={reset}>
                  Cancel
                </Button>
              </SettingsActions>
            </>
          }
        >
          <SettingRow title="Lock this session after" stacked>
            <fieldset className="security-methods">
              <legend className="sr-only">Lock this session after</legend>
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
          </SettingRow>
          {passwordRow('security-timeout-password')}
        </SettingsGroup>
      </form>
    );
  }

  if (flow === 'disable') {
    return (
      <form
        className="set-page"
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
        <SettingsGroup
          title="Turn off app lock"
          headingRef={headingRef}
          description="This session will stay open until you sign out or it expires. You can turn app lock on again at any time."
          footer={
            <>
              <SettingsStatus tone="error">{error}</SettingsStatus>
              <SettingsActions>
                <Button variant="primary" type="submit" disabled={busy || !password}>
                  {busy ? 'Turning off…' : 'Turn off app lock'}
                </Button>
                <Button disabled={busy} onClick={reset}>
                  Cancel
                </Button>
              </SettingsActions>
            </>
          }
        >
          {passwordRow('security-disable-password')}
        </SettingsGroup>
      </form>
    );
  }

  // --------------------------------------------------------------- home rows

  return (
    <SettingsPage>
      <SettingsGroup
        title="App lock"
        description="A quick unlock for this signed-in session on this device, so a glance at your phone does not show your apps. It does not encrypt files on your Vela computer or lock that computer’s screen."
        footer={
          <>
            <SettingsStatus tone="ok">{note}</SettingsStatus>
            <SettingsStatus tone="error">{error}</SettingsStatus>
            {(enrolled || remote) && (
              <SettingsActions>
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
              </SettingsActions>
            )}
          </>
        }
      >
        <SettingRow
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
            <SettingRow
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
            <SettingRow
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
              <SettingRow
                title="Show pattern trail"
                description="Draw a visible line between the dots you connect."
                toggle={{ checked: showTrail, onChange: setShowTrail }}
              />
            )}
          </>
        )}
      </SettingsGroup>
    </SettingsPage>
  );
}
