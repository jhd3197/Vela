import { useRef, useState } from 'react';
import { EngineProvider, useEngine } from '../../src/engine.jsx';
import { api } from '../../src/api.js';
import Button from '../../src/components/ui/Button.jsx';
import Dialog from '../../src/components/ui/Dialog.jsx';
import Drawer from '../../src/components/ui/Drawer.jsx';

const fixture = (window.foundationFixture = { reads: [] });
// Only this development test fixture replaces the API. It never contacts a server.
api.getEngine = ({ signal }) =>
  new Promise((resolve, reject) => {
    fixture.reads.push({ signal, resolve, reject });
  });

function EngineConsumer({ name }) {
  const { engine, engineError, engineRefreshing, refreshEngine } = useEngine();
  return (
    <section>
      <output data-testid={`engine-${name}`}>{engine?.apps_running ?? 'loading'}</output>
      <output data-testid={`engine-error-${name}`}>{engineError?.message ?? ''}</output>
      <Button pending={engineRefreshing} onClick={() => refreshEngine()}>
        Refresh {name}
      </Button>
    </section>
  );
}

function EngineFixture() {
  const [page, setPage] = useState('home');
  const [mounted, setMounted] = useState(true);
  return (
    <section>
      <Button onClick={() => setPage(page === 'home' ? 'settings' : 'home')}>
        Switch engine page
      </Button>
      <Button onClick={() => setMounted((value) => !value)}>Toggle engine provider</Button>
      {mounted && (
        <EngineProvider>
          <EngineConsumer name="shell" />
          <EngineConsumer key={page} name={page} />
        </EngineProvider>
      )}
    </section>
  );
}

function OverlayFixture() {
  const [drawer, setDrawer] = useState(false);
  const [dialog, setDialog] = useState(false);
  const [pending, setPending] = useState(false);
  const closeDrawer = useRef(null);
  const cancelDialog = useRef(null);
  return (
    <section>
      <Button onClick={() => setDrawer(true)}>Open fixture drawer</Button>
      <input aria-label="Background control" />
      <Drawer
        open={drawer}
        onClose={() => setDrawer(false)}
        pending={pending}
        initialFocusRef={closeDrawer}
        aria-label="Fixture drawer"
      >
        <div className="drawer-header">
          <h2>Fixture drawer</h2>
          <Button ref={closeDrawer} disabled={pending} onClick={() => setDrawer(false)}>
            Close fixture drawer
          </Button>
        </div>
        <div className="drawer-body">
          <input aria-label="Drawer control" />
          <Button onClick={() => setDialog(true)}>Open nested dialog</Button>
          <Button onClick={() => setPending((value) => !value)}>Toggle drawer pending</Button>
        </div>
        <Dialog
          open={dialog}
          onClose={() => setDialog(false)}
          pending={pending}
          initialFocusRef={cancelDialog}
          closeOnBackdrop
          aria-label="Nested dialog"
        >
          <h2>Nested dialog</h2>
          <input aria-label="Dialog control" />
          <Button onClick={() => setPending((value) => !value)}>Toggle dialog pending</Button>
          <Button ref={cancelDialog} disabled={pending} onClick={() => setDialog(false)}>
            Cancel nested dialog
          </Button>
        </Dialog>
      </Drawer>
    </section>
  );
}

export default function FoundationFixtures() {
  return (
    <>
      <EngineFixture />
      <OverlayFixture />
    </>
  );
}
