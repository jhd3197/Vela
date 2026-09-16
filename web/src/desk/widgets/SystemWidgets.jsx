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
      <div className="desk-meter-head">
        <span>{host}</span>
        <span>{error ? 'Last reading' : `up ${formatDuration(data.uptime?.seconds)}`}</span>
      </div>
      {history.length > 1 ? (
        <WidgetChart series={history} domain={[0, 100]} height={34} />
      ) : (
        <p className="desk-empty">Collecting CPU readings…</p>
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
        <div className="desk-meter-head">
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
      />
      <p className="desk-stat-caption">{formatBytes(disk.free)} free</p>
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
  return (
    <>
      <WidgetStat
        value={String(data.runsToday ?? 0)}
        caption={data.runsToday === 1 ? 'run today' : 'runs today'}
      />
      <WidgetList
        rows={[
          {
            label: 'Failures',
            detail: String(data.failuresToday ?? 0),
          },
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

  const latest = (data.backups || [])[0] || null;
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

  return (
    <>
      {latest ? (
        <WidgetStat
          value={formatRelativeTime(latest.created_at)}
          caption={`Last backup · ${formatBytes(latest.size)}`}
        />
      ) : (
        <DeskEmpty>No backups yet.</DeskEmpty>
      )}
      {failed ? <p className="desk-empty desk-empty-error">{failed}</p> : null}
      <Button size="small" pending={busy} onClick={runBackup}>
        {busy ? 'Backing up…' : 'Back up now'}
      </Button>
    </>
  );
}
