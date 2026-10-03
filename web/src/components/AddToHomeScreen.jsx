import { useState } from 'react';
import { isAndroid, isIOS, isStandalone, promptInstall, useInstallPrompt } from '../pwa.js';
import IOSInstallSteps from './IOSInstallSteps.jsx';
import AndroidInstallSteps from './AndroidInstallSteps.jsx';
import { SettingRow } from './settings/SettingsKit.jsx';

// "Add to Home Screen" section. Used for web apps in the detail drawer and
// (with forHub) for the hub itself. iOS has no install-prompt API on any
// browser, so it always gets manual steps; Chrome/Edge/Android get a button
// wired to the captured beforeinstallprompt event.
//
// `row` draws the same choice as a settings row (Settings › General); without
// it, it is a drawer section, as the app detail drawer and the phone guide use.
export default function AddToHomeScreen({ appName, forHub = false, row = false }) {
  const installEvent = useInstallPrompt();
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState(null);

  const target = forHub ? 'the Vela hub' : appName;
  const kind = isStandalone()
    ? 'installed'
    : isIOS()
      ? 'ios'
      : installEvent
        ? 'prompt'
        : isAndroid()
          ? 'android'
          : 'manual';

  const install = async () => {
    setBusy(true);
    try {
      setOutcome(await promptInstall(installEvent));
    } catch {
      setOutcome('failed');
    } finally {
      setBusy(false);
    }
  };

  const outcomeNote =
    outcome === 'dismissed'
      ? 'Install dismissed. You can install any time from here.'
      : outcome === 'failed'
        ? 'Use your browser menu to add Vela to your Home Screen.'
        : null;

  if (row) {
    const description = {
      installed: `Already installed. You are running ${target} from the home screen.`,
      ios: 'On iPhone and iPad, Safari adds Vela to the Home Screen.',
      prompt: outcomeNote || `Install ${target} for a full-screen, app-like experience.`,
      android: 'Chrome adds Vela to the Home Screen from its menu.',
      manual: "Choose Add to Home Screen or Install app from your browser's menu.",
    }[kind];
    return (
      <SettingRow
        className="a2hs"
        title="Add to Home Screen"
        description={description}
        control={
          kind === 'prompt' ? (
            <button className="btn btn-primary" onClick={install} disabled={busy}>
              Install app
            </button>
          ) : null
        }
      >
        {kind === 'ios' && <IOSInstallSteps />}
        {kind === 'android' && <AndroidInstallSteps />}
      </SettingRow>
    );
  }

  if (kind === 'installed') {
    return (
      <section className="drawer-section a2hs">
        <h3 className="drawer-section-title">Add to Home Screen</h3>
        <p className="a2hs-note">
          Already installed — you are running {target} from the home screen.
        </p>
      </section>
    );
  }

  if (kind === 'ios') {
    return (
      <section className="drawer-section a2hs">
        <h3 className="drawer-section-title">Add to Home Screen</h3>
        <IOSInstallSteps />
        {!forHub && (
          <p className="a2hs-note">The same steps install the Vela hub itself from this page.</p>
        )}
      </section>
    );
  }

  if (kind === 'prompt') {
    return (
      <section className="drawer-section a2hs">
        <h3 className="drawer-section-title">Add to Home Screen</h3>
        <p className="a2hs-note">Install {target} for a full-screen, app-like experience.</p>
        <button className="btn btn-primary" onClick={install} disabled={busy}>
          Install app
        </button>
        {outcomeNote && (
          <p role="status" className="a2hs-note">
            {outcomeNote}
          </p>
        )}
        {isAndroid() && <AndroidInstallSteps />}
        {!forHub && (
          <p className="a2hs-note">The same flow installs the Vela hub itself from this page.</p>
        )}
      </section>
    );
  }

  if (kind === 'android')
    return (
      <section className="drawer-section a2hs">
        <AndroidInstallSteps />
      </section>
    );

  return (
    <section className="drawer-section a2hs">
      <h3 className="drawer-section-title">Add to Home Screen</h3>
      <p className="a2hs-note">
        Open your browser&apos;s menu and choose <strong>Add to Home Screen</strong> or{' '}
        <strong>Install app</strong> to keep {target} on your home screen.
      </p>
      {!forHub && <p className="a2hs-note">The same applies to the Vela hub itself.</p>}
    </section>
  );
}
