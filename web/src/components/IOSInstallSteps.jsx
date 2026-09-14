import { useState } from 'react';
import { Export, PlusSquare, Compass } from '@phosphor-icons/react';
import { isIOSSafari } from '../phone-setup.js';
import Button from './ui/Button.jsx';

export default function IOSInstallSteps() {
  const [showSteps, setShowSteps] = useState(isIOSSafari);
  const [copyStatus, setCopyStatus] = useState('');
  const link = new URL('/setup', window.location.origin).href;

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(link);
      setCopyStatus('Link copied. Open Safari and paste it into the address bar.');
    } catch {
      setCopyStatus('Touch and hold the address above, copy it, then paste it into Safari.');
    }
  }

  if (!showSteps) {
    return (
      <div className="phone-instructions">
        <Compass size={32} aria-hidden="true" />
        <h3>Open in Safari</h3>
        <p>For these iPhone setup steps, open this page in Safari first.</p>
        <label className="phone-link-label" htmlFor="safari-setup-link">
          Vela setup address
        </label>
        <input
          id="safari-setup-link"
          className="phone-link"
          readOnly
          value={link}
          onFocus={(event) => event.target.select()}
        />
        <Button onClick={copyLink}>Copy link for Safari</Button>
        <p className="phone-note" role="status">
          {copyStatus || 'Copy the link, open Safari, then paste it into the address bar.'}
        </p>
        <Button variant="ghost" onClick={() => setShowSteps(true)}>
          I’m already in Safari
        </Button>
      </div>
    );
  }

  return (
    <div className="phone-instructions">
      <ol className="a2hs-steps phone-steps">
        <li>
          <span className="a2hs-step-num">1</span>
          <div>
            <strong>
              <Export size={19} aria-hidden="true" /> Open the Share menu
            </strong>
            <p>
              In Safari, tap Share (the square with an arrow pointing up). You may need to open the
              More (…) menu first.
            </p>
          </div>
        </li>
        <li>
          <span className="a2hs-step-num">2</span>
          <div>
            <strong>
              <PlusSquare size={19} aria-hidden="true" /> Add to Home Screen
            </strong>
            <p>
              Scroll through the share actions and tap Add to Home Screen. If it’s missing, look in
              Edit Actions.
            </p>
          </div>
        </li>
        <li>
          <span className="a2hs-step-num">3</span>
          <div>
            <strong>Keep Vela one tap away</strong>
            <p>
              Leave Open as Web App on if shown, then tap Add. Open the new Vela icon on your Home
              Screen and sign in if asked.
            </p>
          </div>
        </li>
      </ol>
      <p className="phone-note">
        Keep your Vela computer on and connected while you use your apps.
      </p>
    </div>
  );
}
