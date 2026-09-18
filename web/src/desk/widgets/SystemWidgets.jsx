// The widgets that draw what this computer is doing.
//
// Every number here comes from a real endpoint: `/api/system/metrics` for the
// host, `/api/automations/status` for flows, `/api/backups` for backups. When
// the server cannot answer — no psutil, an unplugged drive, no automation
// runtime — the widget says so rather than showing a zero that looks like a
// measurement.
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api.js';
import Button from '../../components/ui/Button.jsx';
import { Bars } from '../../components/ds/index.js';
import { useDeskData } from '../DeskDataProvider.jsx';
import { formatBytes, formatDuration, formatRelativeTime } from '../metrics.js';
import {
  DeskEmpty,
  DeskFailed,
  DeskLoading,
  WidgetChart,
  WidgetList,
  WidgetMeter,
  WidgetStat,
} from './primitives.jsx';

/** A volume's tone: fine until it is not, and red only once it really is. */
function fullness(percent) {
  if (!Number.isFinite(percent)) return undefined;
  if (percent >= 92) return 'red';
  if (percent >= 78) return 'amber';
  return 'cyan';
}

const NO_PSUTIL =
  'This server cannot read its own CPU and memory. Reinstall the Vela download to restore it.';

export function SystemWidget() {
  const { data, error, loaded } = useDeskData('metrics');
  if (!loaded && !data) return <DeskLoading label="Reading the server…" />;
  if (!data) return <DeskFailed subject="system metrics" />;
  if (!data.available) return <DeskEmpty>{NO_PSUTIL}</DeskEmpty>;

  const history = (data.history || []).map((point) => point.cpu);
  const host = data.host || location.hostname;
  return (
    <>
      <div className="vela-meter-head">
        <span>{host}</span>
        <span>{error ? 'Last reading' : `up ${formatDuration(data.uptime?.seconds)}`}</span>
      </div>
      {history.length > 1 ? (
        <WidgetChart series={history} domain={[0, 100]} height={34} />
      ) : (
        <p className="vela-empty">Collecting CPU readings…</p>
      )}
      <WidgetMeter
        percent={data.memory?.percent}
        label="Memory"
        detail={`${formatBytes(data.memory?.used)} / ${formatBytes(data.memory?.total)}`}
      />
    </>
  );
}

export function VolumeWidget({ cfg = {} }) {
  const { data, loaded } = useDeskData('metrics');
  if (!loaded && !data) return <DeskLoading label="Reading the volume…" />;
  if (!data) return <DeskFailed subject="this volume" />;
  if (!data.available) return <DeskEmpty>{NO_PSUTIL}</DeskEmpty>;

  const disks = data.disks || [];
  if (!cfg.path && disks.length === 0) {
    return (
      <div className="desk-apps-empty">
        <p>No volumes are set up yet.</p>
        <Link className="btn btn-small" to="/settings#desk">
          Choose a volume
        </Link>
      </div>
    );
  }
  const disk = cfg.path ? disks.find((entry) => entry.path === cfg.path) : disks[0];
  if (!disk) {
    return (
      <div className="desk-apps-empty">
        <p>{cfg.path} is no longer one of your volumes.</p>
        <Link className="btn btn-small" to="/settings#desk">
          Desk settings
        </Link>
      </div>
    );
  }
  if (!disk.reachable) {
    return (
      <>
        <div className="vela-meter-head">
          <span>{disk.label}</span>
        </div>
        <DeskEmpty>Not connected right now.</DeskEmpty>
      </>
    );
  }
  return (
    <>
      <WidgetMeter
        percent={disk.percent}
        label={disk.label}
        detail={`${formatBytes(disk.used)} / ${formatBytes(disk.total)}`}
        tone={fullness(disk.percent)}
      />
      <p className="vela-stat-caption">{formatBytes(disk.free)} free</p>
    </>
  );
}

export function FlowsWidget() {
  const { data, loaded } = useDeskData('flows');
  if (!loaded && !data) return <DeskLoading label="Checking flows…" />;
  if (!data) return <DeskFailed subject="your flows" />;
  if (!data.available) {
    return <DeskEmpty>{data.detail || 'Automations are not available on this server.'}</DeskEmpty>;
  }

  const average = data.averageDurationMs;
  // The endpoint answers with today's counts and, where it has them, a run per
  // day for the past week. The bars are the week; the stat is today. A server
  // that only knows about today draws the stat and no chart, rather than a
  // seven-day chart with six days invented.
  const week = Array.isArray(data.runsPerDay)
    ? data.runsPerDay.filter((count) => Number.isFinite(count)).slice(-7)
    : [];
  const failures = data.failuresToday ?? 0;
  return (
    <>
      <WidgetStat
        value={String(data.runsToday ?? 0)}
        caption={data.runsToday === 1 ? 'run today' : 'runs today'}
        delta={failures ? `${failures} failed` : null}
        tone={failures ? 'red' : undefined}
      />
      {week.length > 1 ? (
        <Bars
          series={week}
          caption={failures === 0 ? 'No failures today' : `${failures} failed today`}
        />
      ) : null}
      <WidgetList
        rows={[
          { label: 'Failures', detail: String(failures) },
          {
            label: 'Average run',
            detail:
              average === null || average === undefined
                ? '—'
                : `${Math.round(average / 100) / 10}s`,
          },
        ]}
      />
    </>
  );
}

export function BackupsWidget() {
  const { data, loaded, refresh } = useDeskData('backups');
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState('');

  if (!loaded && !data) return <DeskLoading label="Checking backups…" />;
  if (!data) return <DeskFailed subject="your backups" />;

  // `/api/backups/stats` answers with the newest backup and the schedule, so
  // the widget can say when the next one is without a second request.
  const latest = data.lastSuccessAt
    ? { created_at: data.lastSuccessAt, name: data.lastName }
    : null;
  const nextAt = data.schedule?.nextRunAt;
  const runBackup = async () => {
    setBusy(true);
    setFailed('');
    try {
      await api.createBackup();
      await refresh();
    } catch (error) {
      setFailed(error.message || 'Could not back up.');
    } finally {
      setBusy(false);
    }
  };

  // How far through the gap between the last backup and the next one this
  // moment is. A bar that fills as the next backup approaches says more at a
  // glance than a timestamp, and it is honest: with no schedule there is no
  // progress to show and the meter is left out rather than drawn empty.
  const since = latest ? Date.parse(latest.created_at) : NaN;
  const until = nextAt ? Date.parse(nextAt) : NaN;
  const elapsed =
    Number.isFinite(since) && Number.isFinite(until) && until > since
      ? ((Date.now() - since) / (until - since)) * 100
      : null;

  return (
    <>
      {latest ? (
        <WidgetStat
          value={formatRelativeTime(latest.created_at)}
          caption={
            nextAt
              ? `Last backup · next at ${new Date(nextAt).toLocaleTimeString(undefined, {
                  hour: '2-digit',
                  minute: '2-digit',
                })}`
              : `Last backup · ${data.count} kept, ${formatBytes(data.totalSize)}`
          }
        />
      ) : (
        <DeskEmpty>No backups yet.</DeskEmpty>
      )}
      {elapsed === null ? null : (
        <WidgetMeter
          percent={elapsed}
          label="Until the next one"
          detail={`${data.count} kept`}
          tone="cyan"
        />
      )}
      {failed ? <p className="vela-empty vela-empty-error">{failed}</p> : null}
      <Button size="small" pending={busy} onClick={runBackup}>
        {busy ? 'Backing up…' : 'Back up now'}
      </Button>
    </>
  );
}
