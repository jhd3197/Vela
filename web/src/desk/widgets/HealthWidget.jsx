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
import Ring from '../../components/ds/Ring.jsx';
import { DeskEmpty, DeskFailed, DeskLoading } from './primitives.jsx';

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

  // A proportion, not a count: "eleven of twelve" should read as almost-whole
  // at a glance, and a bar 92 % along reads as a bar. The number in the middle
  // is the checks that passed, because that is the thing being measured; the
  // ring's tone is the worst status among them, because that is the thing
  // worth noticing.
  const considered = summary.considered || checks.length;
  const attention = summary.attention || 0;
  const passing = Math.max(0, considered - attention);
  const tone = summary.fail ? 'red' : attention ? 'amber' : 'green';

  return (
    <>
      <Ring
        percent={considered ? (passing / considered) * 100 : 100}
        value={`${passing}/${considered}`}
        caption={attention ? 'to look at' : 'all good'}
        tone={tone}
        label={`${passing} of ${considered} checks passing`}
      />
      {worst ? <p className="vela-empty">{worst.title}</p> : null}
      <Link className="btn btn-small" to="/settings#health">
        {worst ? 'Fix it' : 'Health'}
      </Link>
    </>
  );
}
