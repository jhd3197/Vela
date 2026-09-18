import React, { useCallback, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import Button from '../../src/components/ui/Button.jsx';
import FormField from '../../src/components/ui/FormField.jsx';
import LoadingState from '../../src/components/ui/LoadingState.jsx';
import { useResource } from '../../src/hooks/useResource.js';
import { useAsyncAction } from '../../src/hooks/useAsyncAction.js';
import { useForm } from '../../src/hooks/useForm.js';
import { useConfirm } from '../../src/hooks/useConfirm.js';
import ConfirmProvider from '../../src/components/ConfirmProvider.jsx';
import '../../src/styles/main.scss';
import FoundationFixtures from './foundations.jsx';
import PrimitiveFixtures from './primitives.jsx';

// Browser-only fixture; no Vela API calls, user data, or production entry point.
const fixture = (window.fixture = {
  reads: [],
  actions: [],
  saves: [],
  completions: 0,
  submissions: 0,
});

function Resource({ id, enabled }) {
  const load = useCallback(
    ({ signal }) =>
      new Promise((resolve, reject) => {
        fixture.reads.push({ id, signal, resolve, reject });
      }),
    [id],
  );
  const resource = useResource(load, { enabled });
  useEffect(() => {
    fixture.refresh = resource.refresh;
  }, [resource.refresh]);
  return (
    <section>
      {resource.loading && <LoadingState>Loading resource…</LoadingState>}
      <output data-testid="resource">{resource.data ?? 'empty'}</output>
      <output data-testid="resource-error">{resource.error?.message ?? ''}</output>
    </section>
  );
}

function Action() {
  const { run, pending, error } = useAsyncAction();
  const submit = async () => {
    const result = await run(
      () =>
        new Promise((resolve, reject) => {
          fixture.actions.push({ resolve, reject });
        }),
    );
    if (result) fixture.completions++;
  };
  useEffect(() => {
    fixture.submit = submit;
  });
  return (
    <section>
      <Button pending={pending} onClick={submit}>
        Run action
      </Button>
      {error && <p role="alert">{error.message}</p>}
    </section>
  );
}

// useForm: one submit at a time, and a server's complaint lands on its field.
function FormFixture() {
  const form = useForm({
    initialValues: { topic: '' },
    validate: (values) => (values.topic.trim() ? {} : { topic: 'Pick a topic.' }),
    onSubmit: (values) =>
      new Promise((resolve, reject) => {
        fixture.saves.push({ values, resolve, reject });
      }),
  });
  return (
    <form data-testid="hook-form" onSubmit={form.handleSubmit}>
      <FormField label="Hook topic" error={form.fieldError('topic')}>
        <input {...form.fieldProps('topic')} />
      </FormField>
      <output data-testid="hook-form-state">
        {[form.dirty ? 'dirty' : 'clean', form.submitting ? 'saving' : 'idle'].join(' ')}
      </output>
      <output data-testid="hook-form-error">{form.formError}</output>
      <Button type="submit" pending={form.submitting}>
        Save topic
      </Button>
      <Button onClick={() => form.reset({ topic: 'saved' })}>Reset topic</Button>
    </form>
  );
}

// useConfirm: one dialog, an answer, and focus back where it started.
function ConfirmFixture() {
  const confirm = useConfirm();
  const [answer, setAnswer] = useState('');
  return (
    <section>
      <Button
        onClick={async () => {
          setAnswer('');
          const said = await confirm({
            title: 'Remove the fixture?',
            message: 'Nothing real is removed.',
            confirmText: 'Remove it',
          });
          setAnswer(said ? 'yes' : 'no');
        }}
      >
        Ask to remove
      </Button>
      <output data-testid="confirm-answer">{answer}</output>
    </section>
  );
}

function App() {
  const [id, setId] = useState('first');
  const [enabled, setEnabled] = useState(true);
  const [mounted, setMounted] = useState(true);
  return (
    <main>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          fixture.submissions++;
        }}
      >
        <Button>Ordinary button</Button>
        <Button type="submit">Submit form</Button>
        <FormField label="Server" hint="Use your local server" error="Enter a server">
          <input defaultValue="localhost" aria-describedby="existing-hint" />
        </FormField>
        <p id="existing-hint">Existing help</p>
        <FormField label="Topic">
          <input />
        </FormField>
      </form>
      <Button onClick={() => setId('second')}>Switch resource</Button>
      <Button onClick={() => setEnabled((value) => !value)}>Toggle resource</Button>
      <Resource id={id} enabled={enabled} />
      <Button onClick={() => setMounted((value) => !value)}>Toggle action</Button>
      {mounted && <Action />}
      <FormFixture />
      <ConfirmFixture />
      <FoundationFixtures />
      <PrimitiveFixtures />
    </main>
  );
}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ConfirmProvider>
      <App />
    </ConfirmProvider>
  </React.StrictMode>,
);
