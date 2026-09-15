import { isIOS } from '../pwa.js';

export default function PhoneCertificateSteps({ connection }) {
  return (
    <div className="phone-instructions">
      <h3>Connect securely to your computer</h3>
      <p>
        This is a one-time step for your home Wi-Fi. Install the certificate from your Vela computer
        before signing in.
      </p>
      <ol className="a2hs-steps phone-steps">
        <li>
          <span className="a2hs-step-num">1</span>
          <div>
            <strong>Download the Vela certificate</strong>
            <p>
              <a className="btn" href={connection.certificate_url}>
                Download certificate
              </a>
            </p>
            <p>
              Before trusting it, compare the downloaded certificate’s SHA-256 fingerprint with the
              one shown under Wi-Fi connection details on your computer.
            </p>
          </div>
        </li>
        <li>
          <span className="a2hs-step-num">2</span>
          <div>
            <strong>Trust it in your phone’s settings</strong>
            {isIOS() ? (
              <p>
                Open Settings → General → VPN &amp; Device Management and install the downloaded
                Vela profile. Then go to General → About → Certificate Trust Settings and enable
                trust for Vela Wi-Fi.
              </p>
            ) : (
              <p>
                In Android Settings, find Security → Encryption &amp; credentials → Install a
                certificate → CA certificate, then choose the downloaded Vela certificate. Names
                vary by phone.
              </p>
            )}
          </div>
        </li>
        <li>
          <span className="a2hs-step-num">3</span>
          <div>
            <strong>Return here and open Vela</strong>
            <p>
              The secure page will explain how to add Vela to your Home Screen. Use the password you
              chose on your computer.
            </p>
          </div>
        </li>
      </ol>
      <a className="btn btn-primary" href={connection.secure_url}>
        Open secure Vela
      </a>
      <p className="phone-note">
        Keep both devices on the same Wi-Fi. If you stop using this Vela computer, remove its
        certificate in your phone’s settings.
      </p>
    </div>
  );
}
