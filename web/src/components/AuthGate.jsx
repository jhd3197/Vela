import { createContext, useContext, useEffect, useState } from 'react';
import { acceptHubSession } from '../api.js';
import { useConfirm } from '../hooks/useConfirm.js';

const AuthContext = createContext({ remote: false, logout() {} });
export const useAuth = () => useContext(AuthContext);

export default function AuthGate({ children }) {
  const confirm = useConfirm();
  const [authenticated, setAuthenticated] = useState(false);
  const [started, setStarted] = useState(false);
  const [checking, setChecking] = useState(true);
  const [remote, setRemote] = useState(false);
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let cancelled = false;
    fetch('/api/session', { headers: { 'X-Vela-Bootstrap': '1' }, cache: 'no-store' })
      .then(async (response) => {
        if (cancelled) return;
        if (!response.ok) {
          if (response.status !== 401)
            setError('The engine is unavailable or this address is not allowed.');
          return;
        }
        const session = await response.json();
        if (cancelled) return;
        acceptHubSession(session.token);
        setRemote(session.remote);
        setAuthenticated(true);
        setStarted(true);
      })
      .catch(() => {
        if (!cancelled) setError('Cannot reach the Vela engine.');
      })
      .finally(() => {
        if (!cancelled) setChecking(false);
      });
    const expired = () => {
      setAuthenticated(false);
      setRemote(true);
    };
    addEventListener('vela:auth-required', expired);
    return () => {
      cancelled = true;
      removeEventListener('vela:auth-required', expired);
    };
  }, []);
  const login = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const response = await fetch('/api/login', {
        method: 'POST',
        headers: { 'X-Vela-Bootstrap': '1', 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      const result = await response.json();
      if (!response.ok)
        throw new Error(typeof result.detail === 'string' ? result.detail : 'Sign-in failed');
      acceptHubSession(result.token);
      setPassword('');
      setRemote(true);
      setAuthenticated(true);
      setStarted(true);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  };
  const logout = async () => {
    const sure = await confirm({
      title: 'Sign out of Vela?',
      message: 'Save any unfinished work first.',
      confirmText: 'Sign out',
    });
    if (!sure) return;
    const response = await fetch('/api/logout', {
      method: 'POST',
      headers: { 'X-Vela-Bootstrap': '1' },
    });
    if (!response.ok) return;
    acceptHubSession(null);
    setAuthenticated(false);
    setStarted(false);
  };
  return (
    <AuthContext.Provider value={{ remote, logout }}>
      {started && children}
      {!authenticated && (
        <div className="auth-screen">
          <form className="auth-card" onSubmit={login}>
            <img src="/vela-mark.png" width="40" height="40" alt="" />
            <h1>{checking ? 'Connecting to Vela…' : 'Your Vela, wherever you are.'}</h1>
            {!checking && (
              <>
                <p>Sign in to your engine to open your apps and data.</p>
                <label htmlFor="vela-password">Password</label>
                <input
                  id="vela-password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                  maxLength={256}
                />
                <button className="btn btn-primary" disabled={busy}>
                  {busy ? 'Signing in…' : 'Sign in'}
                </button>
              </>
            )}
            {error && <p role="alert">{error}</p>}
          </form>
        </div>
      )}
    </AuthContext.Provider>
  );
}
