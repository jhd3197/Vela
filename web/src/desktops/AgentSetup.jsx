// Turning a desktop into one an agent works in.
//
// Four questions, in the words of the plan's section 0.2: what it is called,
// which model runs it, what it may use, and what happens when it wants to change
// something. Everything else — workers, ports, protocols, browser runtimes — is
// Vela's problem and is not asked about here.
//
// What this screen will not do is let somebody set up a desktop that cannot
// work. The runtime and the model are checked before the button is enabled, and
// the reason a choice is unavailable is written next to it rather than
// discovered when the first task fails halfway through.
import { useEffect, useMemo, useState } from 'react';
import Button from '../components/ui/Button.jsx';
import FormField from '../components/ui/FormField.jsx';
import { useApps } from '../store.jsx';
import { desktopsApi } from './desktopsApi.js';

export default function AgentSetup({ desktop, onReady }) {
  const { apps } = useApps();
  const [runtime, setRuntime] = useState(null);
  const [models, setModels] = useState(null);
  const [chosen, setChosen] = useState('');
  const [allowed, setAllowed] = useState([]);
  const [sites, setSites] = useState('');
  const [approvals, setApprovals] = useState('ask');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notes, setNotes] = useState([]);

  useEffect(() => {
    let alive = true;
    Promise.all([desktopsApi.runtime(), desktopsApi.models()])
      .then(([runtimeState, modelState]) => {
        if (!alive) return;
        setRuntime(runtimeState);
        setModels(modelState);
        setChosen((current) => current || modelState.usable?.[0] || '');
      })
      .catch((problem) => alive && setError(problem));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!desktop?.id) return;
    desktopsApi
      .policy(desktop.id)
      .then((policy) => {
        setAllowed(policy.apps || []);
        setSites((policy.sites || []).map((rule) => rule.origin).join('\n'));
        setApprovals(policy.approvals || 'ask');
      })
      .catch(() => {});
  }, [desktop?.id]);

  // Only apps that can actually be driven. A native or legacy app is not
  // something an agent can open in a managed browser, and offering it here
  // would be offering a choice that fails later.
  const usableApps = useMemo(
    () =>
      (apps || []).filter(
        (app) => app.installed && app.schemaVersion === 2 && app.view?.surface === 'embedded',
      ),
    [apps],
  );

  const usableModels = models?.models?.filter((model) => model.tools) || [];
  const blocked =
    (runtime && !runtime.available && runtime.detail) ||
    (models && !models.reachable && models.detail) ||
    (models && models.reachable && !usableModels.length
      ? 'None of the models on this computer can call tools, which is what operating an app needs. Install one — for example: ollama pull qwen3:8b'
      : null);

  const ready = !blocked && chosen && (allowed.length || sites.trim());

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const current = await desktopsApi.policy(desktop.id);
      await desktopsApi.savePolicy(desktop.id, {
        revision: current.revision,
        apps: allowed,
        sites: sites
          .split('\n')
          .map((line) => line.trim())
          .filter(Boolean)
          .map((origin) => ({ origin, includeSubdomains: false })),
        approvals,
        actionScopes: [],
      });
      const result = await desktopsApi.enableAgent(desktop.id);
      setNotes(result.notes || []);
      onReady?.(result);
    } catch (problem) {
      setError(problem);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="agent-setup">
      <header>
        <h2>Let an agent work in {desktop?.name || 'this desktop'}</h2>
        <p>
          This desktop gets its own browser on this computer. It can use only what you allow below,
          and your own windows are not part of it.
        </p>
      </header>

      {blocked && (
        <p className="agent-blocked" role="alert">
          {blocked}
        </p>
      )}

      <FormField
        label="Model"
        hint={
          models?.reachable
            ? 'Only models that can call tools are listed. A bigger model is not automatically better at this.'
            : 'Vela is checking which models are available.'
        }
      >
        <select
          value={chosen}
          onChange={(event) => setChosen(event.target.value)}
          disabled={!usableModels.length}
        >
          {!usableModels.length && <option value="">No usable model</option>}
          {usableModels.map((model) => (
            <option key={model.name} value={model.name}>
              {model.name}
              {model.vision ? ' — can read images' : ''}
            </option>
          ))}
        </select>
      </FormField>

      <fieldset className="agent-allowed">
        <legend>Apps it may use</legend>
        <p className="field-hint">
          Nothing is allowed until you choose it. Opening an app is not the same as letting it
          change anything.
        </p>
        {usableApps.length ? (
          usableApps.map((app) => (
            <label key={app.id} className="agent-choice">
              <input
                type="checkbox"
                checked={allowed.includes(app.id)}
                onChange={(event) =>
                  setAllowed((current) =>
                    event.target.checked
                      ? [...current, app.id]
                      : current.filter((id) => id !== app.id),
                  )
                }
              />
              <span>{app.name}</span>
            </label>
          ))
        ) : (
          <p className="field-hint">No installed app can be used this way yet.</p>
        )}
      </fieldset>

      <FormField
        label="Websites it may open"
        hint="One address per line, for example https://example.com. Leave empty for none."
      >
        <textarea
          value={sites}
          rows={3}
          onChange={(event) => setSites(event.target.value)}
          placeholder="https://example.com"
        />
      </FormField>

      <fieldset className="agent-allowed">
        <legend>Changes</legend>
        <label className="agent-choice">
          <input
            type="radio"
            name="approvals"
            checked={approvals === 'ask'}
            onChange={() => setApprovals('ask')}
          />
          <span>
            <strong>Ask before changes.</strong> Saving, restoring, sending and anything else that
            changes something waits for you.
          </span>
        </label>
        <label className="agent-choice">
          <input
            type="radio"
            name="approvals"
            checked={approvals === 'granted'}
            onChange={() => setApprovals('granted')}
          />
          <span>
            <strong>Allow the actions I pick.</strong> Everything else still asks. You choose which
            actions afterwards; this is not a "trust it with everything" switch.
          </span>
        </label>
      </fieldset>

      {error && (
        <p className="agent-blocked" role="alert">
          {error.message}
        </p>
      )}
      {notes.length > 0 && (
        <ul className="agent-notes">
          {notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}

      <div className="form-actions">
        <Button variant="primary" pending={busy} disabled={!ready || busy} onClick={start}>
          Start the agent
        </Button>
      </div>
    </div>
  );
}
