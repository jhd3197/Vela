import { Link } from 'react-router-dom';
import AddToHomeScreen from '../components/AddToHomeScreen.jsx';
import { dismissWelcome } from '../phone-setup.js';

// Public instructions contain no server data or credentials. Scanning a QR
// should explain Safari before making someone sign in to the wrong browser.
export default function PhoneSetup() {
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
        <AddToHomeScreen appName="Vela" forHub />
        <Link className="btn btn-primary" to="/" onClick={dismissWelcome}>
          Continue to Vela
        </Link>
        <p className="phone-note">Sign in with your Vela password when asked.</p>
      </article>
    </main>
  );
}
