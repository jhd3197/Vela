import { useRef } from 'react';
import { api } from '../api.js';
import { connectedAddress } from '../connected-web.js';
import { useApps } from '../store.jsx';
import { useAsyncAction } from '../hooks/useAsyncAction.js';
import { useForm } from '../hooks/useForm.js';
import Dialog from './ui/Dialog.jsx';
import FormField from './ui/FormField.jsx';
import Button from './ui/Button.jsx';

export default function ConnectedAppForm({ app, onClose }) {
  const initialFocus = useRef(null);
  const { refreshApps, pushToast } = useApps();
  // Removing is not a submit, so it keeps its own pending state; the dialog
  // takes both, because neither may be interrupted.
  const removal = useAsyncAction();

  const form = useForm({
    initialValues: {
      name: app?.name || '',
      url: app?.url || '',
      color: app?.color || '#9184d9',
    },
    validate: (values) => {
      try {
        connectedAddress(values.url.trim(), window.location.origin);
      } catch (failure) {
        return { url: failure.message };
      }
      return {};
    },
    onSubmit: async (values) => {
      const value = { name: values.name.trim(), url: values.url.trim(), color: values.color };
      if (app) await api.updateWebApp(app.id, { ...value, revision: app.revision });
      else await api.addWebApp(value);
      await refreshApps();
      pushToast(app ? 'Connection updated.' : 'Web app added.', 'success');
      onClose();
    },
  });

  const pending = form.submitting || removal.pending;
  const remove = async () => {
    const result = await removal.run(() => api.removeWebApp(app.id, app.revision));
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
      <form onSubmit={form.handleSubmit} className="connected-app-form field">
        <FormField label="App name">
          <input
            ref={initialFocus}
            value={form.values.name}
            onChange={(event) => form.setValue('name', event.target.value)}
            maxLength={80}
            required
            disabled={pending}
            placeholder="My reading app"
          />
        </FormField>
        <FormField
          label="Web address"
          error={form.fieldError('url')}
          hint="Use HTTPS and a different hostname from Vela, reachable from each device. Localhost points to the device displaying the page."
        >
          <input
            type="url"
            value={form.values.url}
            onChange={(event) => form.setValue('url', event.target.value)}
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
            value={form.values.color}
            onChange={(event) => form.setValue('color', event.target.value)}
            disabled={pending}
          />
        </FormField>
        <p className="panel-note">
          Some services block embedding or require sign-in in a browser tab. “Open in browser” is
          always available. Adding this connection does not install the service.
        </p>
        {(form.formError || removal.error) && (
          <p role="alert">{form.formError || removal.error.message}</p>
        )}
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
