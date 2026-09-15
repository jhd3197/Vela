export default function AndroidInstallSteps() {
  return (
    <div className="phone-instructions">
      <h3>On your Android phone</h3>
      <ol className="a2hs-steps phone-steps">
        <li>
          <span className="a2hs-step-num">1</span>
          <div>
            <strong>Open Vela in Chrome</strong>
            <p>If you opened this inside another app, use its menu to open the link in Chrome.</p>
          </div>
        </li>
        <li>
          <span className="a2hs-step-num">2</span>
          <div>
            <strong>Open the browser menu</strong>
            <p>In Chrome, tap the three dots, then Add to Home screen or Install app.</p>
          </div>
        </li>
        <li>
          <span className="a2hs-step-num">3</span>
          <div>
            <strong>Confirm and open Vela</strong>
            <p>
              Tap Install or Add when asked. Open the Vela icon on your Home Screen and sign in if
              needed.
            </p>
          </div>
        </li>
      </ol>
      <p className="phone-note">
        Menu names vary by browser. Keep your Vela computer on and connected.
      </p>
    </div>
  );
}
