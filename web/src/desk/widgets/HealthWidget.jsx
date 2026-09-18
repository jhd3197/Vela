// Whether this server is in good shape, on the desk.
//
// Adapted from ServerKit's `dashboard/SetupHealthWidget.jsx` (MIT, same owner).
// It shows the last sweep and never starts one by looking: a widget that ran
// thirteen checks every time a desk was opened would be a cost, not a glance.
// "Run now" lives in Settings › Health, which this links to.
import { Link } from 'react-router-dom';
import { useState } from 'react';
import { api } from '../../api.js';
import Button from '../../components/ui/Button.jsx';
import { useDeskData } from '../DeskDataProvider.jsx';
import { DeskEmpty, DeskFailed, DeskLoading, WidgetStat } from './primitives.jsx';

export default function HealthWidget() {
  const { data, loaded, refresh } = useDeskData('health');
  const [running, setRunning] = useState(false);

  if (!loaded && !data) return <DeskLoading label="Checking health…" />;
  if (!data) return <DeskFailed subject="this server's health" />;

  const checks = data.checks || [];
  const summary = data.summary || {};
  const worst =
    checks.find((check) => check.status === 'fail') ||
    checks.find((check) => check.status === 'warn') ||
    null;

  const run = async () => {
    setRunning(true);
    try {
      await api.runDoctor();
      await refresh();
    } finally {
      setRunning(false);
    }
  };

  if (checks.length === 0) {
    return (
      <>
        <DeskEmpty>Vela has not checked itself yet.</DeskEmpty>
        <Button size="small" pending={running} onClick={run}>
          Run checks
        </Button>
      </>
    );
  }

  return (
    <>
      <WidgetStat
        value={summary.attention ? `${summary.attention} to look at` : 'All good'}
        caption={worst ? worst.title : `${summary.considered} checks passed`}
        tone={summary.fail ? 'red' : undefined}
      />
      {worst ? <p className="vela-empty">{worst.detail}</p> : null}
      <Link className="btn btn-small" to="/settings#health">
        {worst ? 'Fix it' : 'Health'}
      </Link>
    </>
  );
}
