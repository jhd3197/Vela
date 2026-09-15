import { useRef, useState } from 'react';
import { api } from '../api.js';
import { connectedAddress } from '../connected-web.js';
import { useApps } from '../store.jsx';
import { useAsyncAction } from '../hooks/useAsyncAction.js';
import Dialog from './ui/Dialog.jsx';
import FormField from './ui/FormField.jsx';
import Button from './ui/Button.jsx';

export default function ConnectedAppForm({ app, onClose }) {
  const [name, setName] = useState(app?.name || '');
  const [url, setUrl] = useState(app?.url || '');
  const [color, setColor] = useState(app?.color || '#9184d9');
  const [revision] = useState(app?.revision);
  const [validation, setValidation] = useState('');
  const initialFocus = useRef(null);
  const { refreshApps, pushToast } = useApps();
  const { run, pending, error } = useAsyncAction();

  const submit = async (event) => {
    event.preventDefault();
    setValidation('');
    try {
      connectedAddress(url.trim(), window.location.origin);
    } catch (failure) {
      setValidation(failure.message);
      return;
    }
    const result = await run(() =>
      app
        ? api.updateWebApp(app.id, {
            name: name.trim(),
            url: url.trim(),
            color,
            revision,
          })
        : api.addWebApp({ name: name.trim(), url: url.trim(), color }),
    );
    if (result) {
      await refreshApps();
      pushToast(app ? 'Connection updated.' : 'Web app added.', 'success');
      onClose();
    }
  };
  const remove = async () => {
    const result = await run(() => api.removeWebApp(app.id, revision));
    if (result) {
      await refreshApps();
      pushToast('Connection removed. The service and its data are unchanged.', 'success');
      onClose();
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      pending={pending}
      initialFocusRef={initialFocus}
      className="modal-dialog connected-app-dialog"
      aria-labelledby="connected-app-title"
    >
      <h2 id="connected-app-title">{app ? 'Edit web app' : 'Add existing web app'}</h2>
      <p>Open a service you already run inside Vela. Sign in using that service’s own account.</p>
      <form onSubmit={submit} className="connected-app-form field">
        <FormField label="App name">
          <input
            ref={initialFocus}
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={80}
            required
            disabled={pending}
            placeholder="My reading app"
          />
        </FormField>
        <FormField
          label="Web address"
          error={validation}
          hint="Use HTTPS and a different hostname from Vela, reachable from each device. Localhost points to the device displaying the page."
        >
          <input
            type="url"
            value={url}
            onChange={(event) => {
              setUrl(event.target.value);
              setValidation('');
            }}
            maxLength={2048}
            required
            disabled={pending}
            placeholder="https://reading.example.com"
            autoCapitalize="none"
            spellCheck={false}
          />
        </FormField>
        <FormField label="Icon color">
          <input
            type="color"
            value={color}
            onChange={(event) => setColor(event.target.value)}
            disabled={pending}
          />
        </FormField>
        <p className="panel-note">
          Some services block embedding or require sign-in in a browser tab. “Open in browser” is
          always available. Adding this connection does not install the service.
        </p>
        {error && <p role="alert">{error.message}</p>}
        <div className="connected-app-actions">
          <Button onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" pending={pending}>
            {app ? 'Save changes' : 'Add web app'}
          </Button>
        </div>
      </form>
      {app && (
        <div className="connected-app-remove">
          <p>Removing this connection leaves the service and its data intact.</p>
          <Button variant="danger" pending={pending} onClick={remove}>
            Remove connection
          </Button>
        </div>
      )}
    </Dialog>
  );
}
