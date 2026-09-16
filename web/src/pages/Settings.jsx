import Button from '../components/ui/Button.jsx';
import FormField from '../components/ui/FormField.jsx';
import Dialog from '../components/ui/Dialog.jsx';
import Drawer from '../components/ui/Drawer.jsx';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowsClockwise,
  CaretLeft,
  CaretRight,
  Lock,
  Bell,
  ChartBar,
  PaperPlaneTilt,
  Plus,
  ShieldCheck,
  ArrowCircleUp,
  Stethoscope,
  SquaresFour,
  Trash,
  Palette,
  ChatCircleText,
  Sparkle,
  Info,
  MagnifyingGlass,
  Check,
  Wrench,
  X,
} from '@phosphor-icons/react';
import { api, formatBytes, platformLabel, relTime } from '../api.js';
import { useApps, useEngine } from '../store.jsx';
import { getTheme, setTheme } from '../theme.js';
import { developerToolsPersist, setDeveloperTools, useDeveloperTools } from '../developer.js';
import AddToHomeScreen from '../components/AddToHomeScreen.jsx';
import SecuritySection from '../components/security/SecuritySection.jsx';
import HealthSection from '../components/HealthSection.jsx';
import UpdatesSection from '../components/UpdatesSection.jsx';
import useMediaQuery from '../hooks/useMediaQuery.js';

// The shared compact threshold, named in `_breakpoints.scss`. Below it Settings
// stops being a popup and becomes a screen inside the app.
const COMPACT = '(max-width: 860px)';

// Settings backed by the hub's settings API: theme, chat retention, ntfy
// notifications, local AI model, and backups. Read-only fact grids report
// what the backend actually exposes. Categories follow what a person is trying
// to do; the technical ones are gathered under Developer tools, which appears
// only while that browser-local preference is on.

function StatusPill({ state, text }) {
  return (
    <span className={`pill pill-${state}`}>
      <span
        className={`status-dot${state === 'ok' ? ' status-dot-ok' : state === 'bad' ? ' status-dot-bad' : ''}`}
      />
      {text}
    </span>
  );
}

// ---------------------------------------------------------------- Local AI

function AiSection({ settings, onPatched, onPendingChange }) {
  const { pushToast } = useApps();
  const [ai, setAi] = useState(null);
  const [failed, setFailed] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  useEffect(() => {
    onPendingChange('ai', saving);
    return () => onPendingChange('ai', false);
  }, [saving, onPendingChange]);

  const refresh = () => {
    setRefreshing(true);
    api
      .getAiStatus()
      .then((s) => {
        setAi(s);
        setFailed(false);
      })
      .catch(() => setFailed(true))
      .finally(() => setRefreshing(false));
  };

  useEffect(() => {
    refresh();
  }, []);

  const pickModel = (model) => {
    if (model === settings?.chat_model || saving) return;
    setSaving(true);
    setSaveError('');
    api
      .updateSettings({ chat_model: model })
      .then(() => onPatched({ chat_model: model }))
      .catch((err) => {
        const message = err.message || 'Could not save the model.';
        setSaveError(message);
        pushToast(message);
      })
      .finally(() => setSaving(false));
  };

  return (
    <section className="panel" id="settings-ai">
      <div className="panel-head">
        <h2>Local AI</h2>
        <span className="panel-head-side">
          {ai && (
            <StatusPill
              state={ai.reachable ? 'ok' : 'bad'}
              text={ai.reachable ? 'Connected' : 'Offline'}
            />
          )}
          {failed && !ai && <StatusPill state="bad" text="Status unavailable" />}
          <button
            className="icon-btn"
            onClick={refresh}
            disabled={refreshing}
            aria-label="Refresh AI status"
            title="Refresh AI status"
          >
            <ArrowsClockwise size={16} className={refreshing ? 'spin' : undefined} />
          </button>
        </span>
      </div>
      {ai && (
        <>
          <dl className="fact-grid" style={{ marginBottom: 14 }}>
            <div className="fact">
              <dt>Endpoint</dt>
              <dd className="mono">{ai.url || '—'}</dd>
            </div>
            <div className="fact">
              <dt>Chat model</dt>
              <dd className="mono">{settings?.chat_model || ai.chat_model || '—'}</dd>
            </div>
          </dl>
          {ai.reachable && ai.models?.length > 0 && (
            <div className="chip-row" role="group" aria-label="Chat model">
              {ai.models.map((m) => {
                const current = settings?.chat_model ?? ai.chat_model;
                return (
                  <button
                    key={m}
                    type="button"
                    className={`chip${m === current ? ' chip-active' : ''}`}
                    aria-pressed={m === current}
                    disabled={!settings || saving}
                    onClick={() => pickModel(m)}
                  >
                    {m}
                  </button>
                );
              })}
            </div>
          )}
          {ai.reachable && ai.models?.length === 0 && (
            <p className="panel-note">
              No models found — pull one first, e.g.{' '}
              <code className="mono">ollama pull qwen3:8b</code>.
            </p>
          )}
          {ai.model_available === false && (
            <p className="panel-note" style={{ marginTop: 8 }}>
              The selected model isn't installed yet — pick one above or pull it first.
            </p>
          )}
          {ai.hint && (
            <p className="panel-note" style={{ marginTop: 8 }}>
              {ai.hint}
            </p>
          )}
        </>
      )}
      {!ai && !failed && <p className="panel-note">Checking the local AI runtime…</p>}
      {failed && !ai && <p className="panel-note">Couldn't reach the status endpoint.</p>}
      {saveError && (
        <p className="inline-error" role="alert">
          {saveError}
        </p>
      )}
    </section>
  );
}

// ------------------------------------------------------------- Notifications

const NTFY_EVENTS = [
  { key: 'digest', label: 'Hub digest', icon: ChartBar },
  { key: 'status_alerts', label: 'Status alerts', icon: Bell },
];

function NotificationsSection({ settings, onPatched, onPendingChange }) {
  const { pushToast } = useApps();
  const ntfy = settings?.ntfy_config || {};
  const events = ntfy.events || {};
  const [form, setForm] = useState({ server: '', topic: '', user: '', pass: '' });
  const [sending, setSending] = useState(false);
  const [note, setNote] = useState(null); // { kind: 'ok' | 'err', text }
  const dirty = useRef(false);
  useEffect(() => {
    onPendingChange('notifications', sending);
    return () => onPendingChange('notifications', false);
  }, [sending, onPendingChange]);

  useEffect(() => {
    if (settings && !dirty.current) {
      setForm({
        server: ntfy.server || '',
        topic: ntfy.topic || '',
        user: ntfy.user || '',
        pass: '',
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings]);

  const change = (patch) => {
    setForm((f) => ({ ...f, ...patch }));
    dirty.current = true;
    setNote({ kind: 'ok', text: 'Unsaved changes.' });
  };

  const patchNtfy = (patch, successText, localPatch) => {
    if (sending) return;
    setSending(true);
    api
      .updateSettings({ ntfy_config: patch })
      .then(() => {
        onPatched({ ntfy_config: localPatch || patch });
        if (successText) pushToast(successText, 'success');
        setNote({
          kind: 'ok',
          text:
            successText ||
            (dirty.current
              ? 'Event preference saved. Connection details have unsaved changes.'
              : 'Notification preference saved.'),
        });
      })
      .catch((err) =>
        setNote({ kind: 'err', text: err.message || 'Could not save notification settings.' }),
      )
      .finally(() => setSending(false));
  };

  const save = async (sendTest) => {
    if (sending) return;
    setSending(true);
    setNote({ kind: 'ok', text: sendTest ? 'Saving and sending…' : 'Saving…' });
    try {
      const patch = {
        server: form.server.trim(),
        topic: form.topic.trim(),
        user: form.user.trim(),
        // The password is write-only: only send it when the user typed one.
        ...(form.pass ? { pass: form.pass } : {}),
      };
      await api.updateSettings({ ntfy_config: patch });
      onPatched({ ntfy_config: { ...patch, passConfigured: ntfy.passConfigured || !!form.pass } });
      dirty.current = false;
      setForm((f) => ({ ...f, pass: '' }));
      if (!sendTest) {
        setNote({ kind: 'ok', text: 'Notification settings saved.' });
        return;
      }
      const receipt = await api.testNotify();
      setNote({
        kind: 'ok',
        text: `Accepted by ntfy at ${new Date(receipt.accepted_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}. Check your phone — server acceptance doesn't confirm delivery.`,
      });
    } catch (err) {
      setNote({ kind: 'err', text: err.message || 'Test notification failed.' });
    } finally {
      setSending(false);
    }
  };

  const live = !!(form.server.trim() && form.topic.trim());

  return (
    <section className="panel" id="settings-notifications">
      <div className="panel-head">
        <h2>Notifications</h2>
        <StatusPill state={live ? 'ok' : 'idle'} text={live ? 'Configured' : 'Not set up'} />
      </div>
      <p className="panel-note" style={{ marginBottom: 14 }}>
        {form.topic.trim() ? (
          <>
            Subscribe to{' '}
            <b className="mono">
              {form.server.trim() || 'https://ntfy.sh'}/{form.topic.trim()}
            </b>{' '}
            in the ntfy app on your phone.
          </>
        ) : (
          'Push events to your phone through any ntfy server. Subscribe to the topic in the ntfy app.'
        )}
      </p>
      <div className="form-grid">
        <div className="field">
          <FormField label="Server">
            <input
              id="ntfy-server"
              value={form.server}
              disabled={sending}
              onChange={(e) => change({ server: e.target.value })}
              placeholder="https://ntfy.sh"
              autoComplete="off"
            />
          </FormField>
        </div>
        <div className="field">
          <FormField label="Topic">
            <input
              id="ntfy-topic"
              value={form.topic}
              disabled={sending}
              onChange={(e) => change({ topic: e.target.value })}
              placeholder="my-vela-alerts"
              autoComplete="off"
            />
          </FormField>
        </div>
        <div className="field">
          <FormField label="User (optional)">
            <input
              id="ntfy-user"
              value={form.user}
              disabled={sending}
              onChange={(e) => change({ user: e.target.value })}
              autoComplete="off"
            />
          </FormField>
        </div>
        <div className="field">
          <label htmlFor="ntfy-pass">Password</label>
          <div className="field-side">
            <input
              id="ntfy-pass"
              type="password"
              value={form.pass}
              disabled={sending}
              onChange={(e) => change({ pass: e.target.value })}
              placeholder={ntfy.passConfigured ? 'Configured — type to replace' : '••••••'}
              autoComplete="new-password"
            />
            {ntfy.passConfigured && (
              <Button
                type="button"
                size="small"
                variant="ghost"
                disabled={sending}
                onClick={() =>
                  patchNtfy({ pass: '' }, 'ntfy password cleared.', { passConfigured: false })
                }
              >
                Clear
              </Button>
            )}
          </div>
        </div>
      </div>
      <div
        className="chip-row"
        role="group"
        aria-label="Notification events"
        style={{ marginTop: 14 }}
      >
        {NTFY_EVENTS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            className={`chip${events[key] ? ' chip-active' : ''}`}
            aria-pressed={!!events[key]}
            disabled={sending}
            onClick={() => patchNtfy({ events: { [key]: !events[key] } })}
          >
            <Icon size={14} />
            {label}
          </button>
        ))}
      </div>
      <div className="form-actions">
        <Button size="small" disabled={sending || !dirty.current} onClick={() => save(false)}>
          Save
        </Button>
        <Button
          size="small"
          variant="primary"
          disabled={sending || !live}
          onClick={() => save(true)}
        >
          <PaperPlaneTilt size={14} />
          {sending ? 'Working…' : 'Send test'}
        </Button>
        {note && (
          <span
            className={note.kind === 'err' ? 'inline-error' : 'saved-note'}
            role={note.kind === 'err' ? 'alert' : 'status'}
          >
            {note.text}
          </span>
        )}
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ Backups

function BackupsSection({ onPendingChange }) {
  const { pushToast } = useApps();
  const [backups, setBackups] = useState(null);
  const [stats, setStats] = useState(null);
  const [creating, setCreating] = useState(false);
  const [verifying, setVerifying] = useState(null); // name being verified
  const [restoring, setRestoring] = useState(null); // name being restored
  const [confirmName, setConfirmName] = useState(''); // typed to confirm
  const [drawer, setDrawer] = useState(null); // the backup being restored
  const [results, setResults] = useState({}); // name -> { ok, text }
  const [note, setNote] = useState(null);
  const [savingSchedule, setSavingSchedule] = useState(false);
  const cancelRef = useRef(null);

  const busy = creating || verifying !== null || restoring !== null || savingSchedule;
  useEffect(() => {
    onPendingChange('backups', busy);
    return () => onPendingChange('backups', false);
  }, [busy, onPendingChange]);

  const refresh = () => {
    api
      .getBackups()
      .then((data) => setBackups(data.backups || []))
      .catch(() => setBackups([]));
    api
      .backupStats()
      .then(setStats)
      .catch(() => setStats(null));
  };

  useEffect(() => {
    refresh();
  }, []);

  const schedule = stats?.schedule;

  const saveSchedule = (change) => {
    setSavingSchedule(true);
    setNote(null);
    api
      .updateSettings({ backups: { schedule: { ...schedule, ...change } } })
      .then(() => refresh())
      .catch((err) => setNote({ kind: 'err', text: err.message || 'Could not save the schedule.' }))
      .finally(() => setSavingSchedule(false));
  };

  const create = () => {
    setCreating(true);
    setNote(null);
    api
      .createBackup()
      .then((b) => {
        pushToast(`Backup ${b.name} created (${formatBytes(b.size)}).`, 'success');
        setNote({ kind: 'ok', text: `Backup ${b.name} created (${formatBytes(b.size)}).` });
        refresh();
      })
      .catch((err) => setNote({ kind: 'err', text: err.message || 'Backup failed.' }))
      .finally(() => setCreating(false));
  };

  const verify = (name) => {
    setVerifying(name);
    api
      .verifyBackup(name)
      .then((r) => {
        const manifests = r.manifests?.length ?? 0;
        const files = r.files?.length ?? 0;
        setResults((prev) => ({
          ...prev,
          [name]: r.ok
            ? {
                ok: true,
                text: `Restore drill passed — ${files} file${files === 1 ? '' : 's'}, ${manifests} app manifest${manifests === 1 ? '' : 's'} checked.`,
              }
            : { ok: false, text: 'Restore drill failed — this backup may not restore cleanly.' },
        }));
      })
      .catch((err) =>
        setResults((prev) => ({
          ...prev,
          [name]: { ok: false, text: err.message || 'Verify failed.' },
        })),
      )
      .finally(() => setVerifying(null));
  };

  const restore = () => {
    const name = drawer;
    setRestoring(name);
    setNote(null);
    api
      .restoreBackup(name)
      .then((result) => {
        setDrawer(null);
        setConfirmName('');
        pushToast(`Restored ${name}.`, 'success');
        setNote({
          kind: 'ok',
          text:
            `Restored ${name}. A copy of what it replaced was saved as ${result.safety}.` +
            (result.failedToRestart?.length
              ? ` ${result.failedToRestart.join(', ')} did not start again — open it to try.`
              : ''),
        });
        refresh();
      })
      .catch((err) => setNote({ kind: 'err', text: err.message || 'Restore failed.' }))
      .finally(() => setRestoring(null));
  };

  return (
    <>
      <section className="panel" id="settings-backups">
        <div className="panel-head">
          <h2>Backups</h2>
          <Button size="small" variant="primary" disabled={busy} onClick={create}>
            <Plus size={14} />
            {creating ? 'Creating…' : 'Create backup'}
          </Button>
        </div>

        {/* What is protected right now, before anything about how. */}
        <dl className="fact-grid">
          <div className="fact">
            <dt>Last backup</dt>
            <dd>{stats?.lastSuccessAt ? relTime(stats.lastSuccessAt) : 'Never'}</dd>
          </div>
          <div className="fact">
            <dt>Next backup</dt>
            <dd>
              {schedule?.nextRunAt
                ? new Date(schedule.nextRunAt).toLocaleString(undefined, {
                    weekday: 'short',
                    hour: '2-digit',
                    minute: '2-digit',
                  })
                : 'Not scheduled'}
            </dd>
          </div>
          <div className="fact">
            <dt>Kept</dt>
            <dd>{stats ? `${stats.count} · ${formatBytes(stats.totalSize)}` : '—'}</dd>
          </div>
        </dl>

        <p className="panel-note">
          A backup copies your settings, which apps are installed and what they saved. Logs and
          wallpapers are not included. Verify runs a restore drill in an isolated folder — your live
          data is never touched by it.
        </p>

        {note && (
          <p
            className={note.kind === 'err' ? 'inline-error' : 'saved-note'}
            role={note.kind === 'err' ? 'alert' : 'status'}
          >
            {note.text}
          </p>
        )}

        {/* The schedule. Off by default: Vela does not start writing copies on
            a timer until someone asks it to. */}
        <div className="settings-row">
          <div>
            <h3 id="backup-schedule-label">Back up automatically</h3>
            <p>
              Once a day, at the time you choose
              {schedule?.timezone ? `, by this computer's clock (${schedule.timezone})` : ''}. A run
              missed because Vela was off is not made up later.
            </p>
          </div>
          <button
            className={`switch${schedule?.enabled ? ' switch-on' : ''}`}
            role="switch"
            aria-checked={Boolean(schedule?.enabled)}
            aria-labelledby="backup-schedule-label"
            disabled={busy || !schedule}
            onClick={() => saveSchedule({ enabled: !schedule.enabled })}
          />
        </div>

        {schedule?.enabled && (
          <div className="field-row">
            <FormField label="At">
              <input
                type="time"
                value={schedule.time}
                disabled={busy}
                onChange={(event) => saveSchedule({ time: event.target.value })}
              />
            </FormField>
            <FormField label="Backups to keep" hint="Older ones are removed automatically.">
              <input
                type="number"
                min="1"
                max="100"
                value={schedule.keep}
                disabled={busy}
                onChange={(event) => saveSchedule({ keep: Number(event.target.value) })}
              />
            </FormField>
          </div>
        )}

        {backups === null && <p className="panel-note">Checking…</p>}
        {backups !== null && backups.length === 0 && (
          <p className="panel-note">No backups yet. Create the first snapshot.</p>
        )}
        {backups !== null && backups.length > 0 && (
          <ul className="mini-list">
            {backups.map((b) => {
              const result = results[b.name];
              return (
                <li key={b.name} className="backup-row">
                  <ShieldCheck
                    size={16}
                    className={result ? (result.ok ? 'backup-ok' : 'backup-bad') : undefined}
                  />
                  <span className="backup-main">
                    <span className="mini-list-name mono">{b.name}</span>
                    <span className="backup-meta">
                      {relTime(b.created_at)} · {formatBytes(b.size)}
                      {b.safety ? ' · taken before a restore' : ''}
                      {result && (
                        <span className={result.ok ? 'backup-note-ok' : 'backup-note-bad'}>
                          {' '}
                          — {result.text}
                        </span>
                      )}
                    </span>
                  </span>
                  <Button size="small" disabled={busy} onClick={() => verify(b.name)}>
                    {verifying === b.name ? 'Verifying…' : 'Verify'}
                  </Button>
                  <Button
                    size="small"
                    disabled={busy}
                    onClick={() => {
                      setConfirmName('');
                      setDrawer(b.name);
                    }}
                  >
                    Restore
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {/* Restoring replaces live files and stops running apps, so it says so in
          full and asks for the name to be typed. */}
      <Drawer
        open={Boolean(drawer)}
        onClose={() => setDrawer(null)}
        pending={restoring !== null}
        initialFocusRef={cancelRef}
        aria-label="Restore a backup"
      >
        <h2>Restore {drawer}?</h2>
        <p className="panel-note">This puts back, as they were in that backup:</p>
        <ul className="panel-note restore-list">
          <li>your hub settings</li>
          <li>everything your apps saved</li>
          <li>which apps are installed, and their versions</li>
        </ul>
        <p className="panel-note">
          Your logs, wallpapers and chats are left alone. Any app running right now is stopped first
          and started again afterwards. Before anything is replaced, Vela checks that this backup
          reads correctly and saves a copy of what it is about to replace — so you can come back
          from this.
        </p>
        <FormField
          label={`Type ${drawer} to confirm`}
          hint="Restoring cannot be undone in one click, so it takes the name."
        >
          <input
            value={confirmName}
            autoComplete="off"
            disabled={restoring !== null}
            onChange={(event) => setConfirmName(event.target.value)}
          />
        </FormField>
        <div className="actions">
          <Button ref={cancelRef} disabled={restoring !== null} onClick={() => setDrawer(null)}>
            Cancel
          </Button>
          <Button
            variant="danger"
            pending={restoring !== null}
            disabled={confirmName.trim() !== drawer}
            onClick={restore}
          >
            Restore this backup
          </Button>
        </div>
      </Drawer>
    </>
  );
}

// --------------------------------------------------------------------- Desk

// A volume is a folder on this computer that the desk may report the free
// space of. Vela does not enumerate the machine's drives: nothing appears on
// the desk that the user did not name here, and the server refuses a path that
// is not a folder rather than storing it and failing inside a widget later.
function DeskSection({ settings, onPatched, onPendingChange }) {
  const volumes = settings?.desk?.volumes || [];
  const [draft, setDraft] = useState({ path: '', label: '' });
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState('');

  useEffect(() => {
    onPendingChange('desk', saving);
    return () => onPendingChange('desk', false);
  }, [saving, onPendingChange]);

  const save = (next) => {
    setSaving(true);
    setNote('');
    return api
      .updateSettings({ desk: { volumes: next } })
      .then(() => {
        onPatched({ desk: { ...(settings?.desk || {}), volumes: next } });
        setDraft({ path: '', label: '' });
      })
      .catch((error) => setNote(error.message || 'Could not save the volume.'))
      .finally(() => setSaving(false));
  };

  const add = (event) => {
    event.preventDefault();
    const path = draft.path.trim();
    if (!path) return;
    save([...volumes, { path, label: draft.label.trim() }]);
  };

  return (
    <section className="panel" id="settings-desk">
      <div className="panel-head">
        <h2>Volumes</h2>
      </div>
      <p className="panel-note" style={{ marginBottom: 14 }}>
        Add a folder here to put a Volume widget for it on your desk. Vela only reports how full it
        is — it does not read what is inside.
      </p>
      {volumes.length > 0 && (
        <ul className="settings-volume-list">
          {volumes.map((volume) => (
            <li key={volume.path}>
              <span className="settings-volume-text">
                <span>{volume.label || volume.path}</span>
                <small className="mono">{volume.path}</small>
              </span>
              <Button
                size="icon"
                variant="ghost"
                aria-label={`Remove ${volume.label || volume.path}`}
                disabled={saving}
                onClick={() => save(volumes.filter((entry) => entry.path !== volume.path))}
              >
                <Trash size={16} aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <form className="form-grid" onSubmit={add}>
        <div className="field">
          <FormField label="Folder">
            <input
              id="desk-volume-path"
              value={draft.path}
              disabled={saving}
              placeholder="D:\Media"
              autoComplete="off"
              onChange={(event) => setDraft((prev) => ({ ...prev, path: event.target.value }))}
            />
          </FormField>
        </div>
        <div className="field">
          <FormField label="Name (optional)">
            <input
              id="desk-volume-label"
              value={draft.label}
              disabled={saving}
              placeholder="Media"
              autoComplete="off"
              onChange={(event) => setDraft((prev) => ({ ...prev, label: event.target.value }))}
            />
          </FormField>
        </div>
      </form>
      {note && (
        <p className="inline-error" role="alert">
          {note}
        </p>
      )}
      <div className="form-actions">
        <Button pending={saving} disabled={!draft.path.trim()} onClick={add}>
          Add volume
        </Button>
      </div>
    </section>
  );
}

// --------------------------------------------------------------------- page

const SECTIONS = [
  {
    id: 'general',
    label: 'General',
    icon: Info,
    description: 'Your server, your devices, and how much Vela shows.',
    keywords: 'version platform phone install home screen about developer tools logs system',
  },
  {
    id: 'desk',
    label: 'Desk',
    icon: SquaresFour,
    description: 'What your home board is allowed to show.',
    keywords: 'desk widgets volumes drive disk wallpaper home board',
  },
  {
    id: 'appearance',
    label: 'Appearance',
    icon: Palette,
    description: 'Make Vela feel at home.',
    keywords: 'theme light dark',
  },
  {
    id: 'security',
    label: 'Security',
    icon: Lock,
    description: 'Lock this session when you put your phone down.',
    keywords: 'lock pin pattern unlock password sign out inactivity passcode',
  },
  {
    id: 'chat',
    label: 'Chat & privacy',
    icon: ChatCircleText,
    description: 'Choose what this browser remembers.',
    keywords: 'history retention conversation',
  },
  {
    id: 'ai',
    label: 'Local AI',
    icon: Sparkle,
    description: 'Connect with the models on your server.',
    keywords: 'ollama model endpoint',
  },
  {
    id: 'notifications',
    label: 'Notifications',
    icon: Bell,
    description: 'Keep up with your server, wherever you are.',
    keywords: 'ntfy push topic alerts',
  },
  {
    id: 'health',
    label: 'Health',
    icon: Stethoscope,
    description: 'Check that this server has what it needs, and repair what Vela can.',
    keywords: 'doctor checks repair diagnose disk space certificate runtime stale orphan',
  },
  {
    id: 'updates',
    label: 'Updates',
    icon: ArrowCircleUp,
    description: 'Keep this server current, and choose what it checks.',
    keywords: 'update upgrade version release notes github download automatic',
  },
  {
    id: 'backups',
    label: 'Backups & storage',
    icon: ShieldCheck,
    description: 'Keep a recoverable copy, and see what Vela is using.',
    keywords: 'restore snapshot verify disk space storage used',
  },
  {
    id: 'developer',
    label: 'Developer tools',
    icon: Wrench,
    developer: true,
    description: 'Logs, system details and the tools for developing apps.',
    keywords: 'developer logs system environments network endpoint api data directory diagnostics',
  },
];

// Earlier releases linked to their own sections. Those bookmarks keep working:
// the technical ones land in Developer tools, which offers to turn the
// preference on rather than switching it on by itself.
const SECTION_ALIASES = {
  environments: 'developer',
  network: 'developer',
  storage: 'backups',
};

function resolveSection(id) {
  const wanted = SECTION_ALIASES[id] || id;
  return SECTIONS.some((s) => s.id === wanted) ? wanted : 'general';
}

export default function Settings({ initialSection = 'appearance', explicit = false, onClose }) {
  const { platform, pushToast } = useApps();
  const { engine } = useEngine();
  const developer = useDeveloperTools();
  const compact = useMediaQuery(COMPACT);
  const [active, setActive] = useState(() => resolveSection(initialSection));
  // Compact screens open on the category list unless the caller, a bookmark or
  // a search result asked for one section. Wide screens keep the two-pane popup
  // and ignore this entirely, so one rotation never loses a draft.
  const [listed, setListed] = useState(() => !explicit);
  const [query, setQuery] = useState('');
  // A section can own a deeper screen (app-lock setup). It hands back the way
  // out so Back, Escape and the phone's own back gesture all agree.
  const [subScreen, setSubScreen] = useState(null);
  const backRef = useRef(null);
  const closeRef = useRef(null);
  const contentRef = useRef(null);
  const headingRef = useRef(null);
  const navRef = useRef(null);
  const lockedRef = useRef(null);
  const [health, setHealth] = useState(null);
  const [settings, setSettings] = useState(null);
  const [theme, setThemeState] = useState(getTheme);
  const [loadError, setLoadError] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [pendingSections, setPendingSections] = useState({});
  const onPendingChange = useCallback((section, pending) => {
    setPendingSections((previous) => ({ ...previous, [section]: pending }));
  }, []);
  // A section reports the way out of its own deeper screen, or null when it
  // has none. Back and Escape use it before stepping up to the category list.
  const onSubScreen = useCallback((back) => {
    backRef.current = back;
    setSubScreen(Boolean(back));
  }, []);
  const pending = saving || Object.values(pendingSections).some(Boolean);
  const locked = active === 'developer' && !developer;

  useEffect(() => {
    api
      .health()
      .then(setHealth)
      .catch(() => {});
    api
      .getSettings()
      .then((s) => {
        setSettings(s);
        if (s.theme === 'dark' || s.theme === 'light') setThemeState(s.theme);
      })
      .catch(() => setLoadError(true));
  }, []);

  useEffect(() => {
    contentRef.current?.scrollTo(0, 0);
  }, [active, listed]);

  // Moving between phone screens carries focus with the reader: into the
  // section's heading on the way in, back onto the row they chose on the way
  // out. Opening the popup itself is left to the dialog's initial focus.
  const opened = useRef(false);
  useEffect(() => {
    if (!compact) return;
    if (!opened.current) {
      opened.current = true;
      return;
    }
    if (listed)
      navRef.current?.querySelector(`[data-section="${active}"]`)?.focus({ preventScroll: true });
    else headingRef.current?.focus({ preventScroll: true });
  }, [compact, listed, active]);

  // Losing the tools — here or in another tab — leaves the explanation in
  // focus instead of an empty panel. The section, and the way back, stay put.
  useEffect(() => {
    if (locked) lockedRef.current?.focus();
  }, [locked]);

  // Merge a successful PATCH into local settings state (deep for nested dicts).
  const onPatched = (patch) => {
    // Ask stays mounted beneath the popup; its retention choice must update too.
    if ('chat_history' in patch || 'chat_model' in patch) {
      window.dispatchEvent(new CustomEvent('vela:chat-settings', { detail: patch }));
    }
    setSettings((prev) => {
      if (!prev) return prev;
      const next = { ...prev };
      for (const [key, value] of Object.entries(patch)) {
        if (value && typeof value === 'object' && !Array.isArray(value)) {
          next[key] = { ...(prev[key] || {}), ...value };
          if (key === 'ntfy_config' && value.events) {
            next[key].events = { ...(prev.ntfy_config?.events || {}), ...value.events };
          }
        } else {
          next[key] = value;
        }
      }
      return next;
    });
  };

  const pickTheme = (next) => {
    if (saving) return;
    const previous = theme;
    setSaving(true);
    setSaveError('');
    setTheme(next); // instant local apply (also updates the localStorage fallback)
    setThemeState(next);
    api
      .updateSettings({ theme: next })
      .catch((err) => {
        setTheme(previous);
        setThemeState(previous);
        setSaveError(err.message || 'Could not save the theme.');
      })
      .finally(() => setSaving(false));
  };

  const chatHistory = settings?.chat_history !== false;
  const pickChatHistory = (on) => {
    if (saving) return;
    setSaving(true);
    setSaveError('');
    if (!on) {
      try {
        localStorage.removeItem('vela-chat');
      } catch {
        // Private mode etc. — nothing else to wipe.
      }
    }
    onPatched({ chat_history: on });
    api
      .updateSettings({ chat_history: on })
      .then(() =>
        pushToast(
          on ? 'Chat history kept on this device.' : 'Chat history wiped from this device.',
          'success',
        ),
      )
      .catch((err) => {
        onPatched({ chat_history: !on });
        setSaveError(err.message || 'Could not save the setting.');
      })
      .finally(() => setSaving(false));
  };

  const section = SECTIONS.find((s) => s.id === active);
  const visible = SECTIONS.filter((s) => !s.developer || developer);
  const matches = visible.filter((s) =>
    `${s.label} ${s.keywords}`.toLowerCase().includes(query.trim().toLowerCase()),
  );
  // One list screen on a phone, one section at a time beneath it.
  const onList = compact && listed;

  // Back, Escape and the phone's own back gesture all walk the same visible
  // hierarchy: setup step, then section, then the list, then the workspace.
  // The dialog owns dismissal, so there is no second history handler here.
  const dismiss = () => {
    if (backRef.current) {
      backRef.current();
      return;
    }
    if (compact && !listed) {
      setListed(true);
      return;
    }
    onClose();
  };
  const openSection = (id) => {
    setActive(id);
    setListed(false);
  };

  return (
    <Dialog
      open
      pending={pending}
      onClose={dismiss}
      closeOnBackdrop={!compact}
      className={`modal-dialog settings-dialog${compact ? ' settings-screen' : ''}${
        onList ? ' is-list' : ''
      }`}
      initialFocusRef={closeRef}
      aria-labelledby="settings-title"
    >
      <aside className="settings-sidebar">
        <div className="settings-list-head">
          <h1 id="settings-title">Settings</h1>
          {compact && (
            <button
              ref={onList ? closeRef : null}
              type="button"
              className="icon-btn"
              aria-label="Close settings"
              disabled={pending}
              onClick={onClose}
            >
              <X size={19} />
            </button>
          )}
        </div>
        <div className="settings-search">
          <MagnifyingGlass size={15} aria-hidden="true" />
          <input
            type="search"
            aria-label="Find a setting"
            placeholder="Find a setting…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <nav aria-label="Settings categories" ref={navRef}>
          {matches.map(({ id, label, icon: Icon, description }) => (
            <button
              key={id}
              type="button"
              data-section={id}
              className={`settings-nav-item${active === id && !compact ? ' is-active' : ''}`}
              aria-current={active === id && !compact ? 'true' : undefined}
              onClick={() => openSection(id)}
            >
              <Icon size={compact ? 20 : 17} />
              <span className="settings-nav-label">
                {label}
                {compact && <small>{description}</small>}
              </span>
              {compact && <CaretRight size={16} aria-hidden="true" className="settings-nav-go" />}
            </button>
          ))}
          {matches.length === 0 && (
            <p className="settings-no-results" role="status">
              No settings found.
            </p>
          )}
        </nav>
        <div className="settings-brand">
          <img src="/vela-mark.png" alt="" width="24" height="24" />
          <span>
            Vela <small>{health?.version ? `v${health.version}` : 'Personal app server'}</small>
          </span>
        </div>
      </aside>
      <div className="settings-main">
        <header className="settings-header">
          {compact && (
            <button
              type="button"
              className="icon-btn settings-back"
              aria-label={subScreen ? 'Back to Security' : 'Back to Settings'}
              disabled={pending}
              onClick={dismiss}
            >
              <CaretLeft size={20} />
            </button>
          )}
          <div>
            <h2 tabIndex={-1} ref={headingRef}>
              {section.label}
            </h2>
            <p>{section.description}</p>
          </div>
          <button
            ref={onList ? null : closeRef}
            type="button"
            className="icon-btn"
            aria-label="Close settings"
            disabled={pending}
            onClick={onClose}
          >
            <X size={19} />
          </button>
        </header>
        <div className="settings-content" ref={contentRef}>
          {loadError && (
            <p className="inline-error" role="alert">
              Could not load settings. Close this window and try again.
            </p>
          )}

          <section className="panel" id="settings-general" hidden={active !== 'general'}>
            <div className="panel-head">
              <h2>General</h2>
            </div>
            <div className="settings-row">
              <div>
                <h3 id="developer-tools-label">Show developer tools</h3>
                <p>Show app logs, system details, and tools for developing apps in this browser.</p>
                {!developerToolsPersist() && (
                  <p className="panel-note">
                    This browser is not storing preferences, so the choice lasts until you close the
                    tab.
                  </p>
                )}
              </div>
              <div className="seg" role="group" aria-labelledby="developer-tools-label">
                {[true, false].map((value) => (
                  <button
                    key={String(value)}
                    type="button"
                    aria-pressed={developer === value}
                    disabled={pending}
                    className={`seg-opt${developer === value ? ' seg-opt-active' : ''}`}
                    onClick={() => setDeveloperTools(value)}
                  >
                    {value ? 'On' : 'Off'}
                  </button>
                ))}
              </div>
            </div>
            <dl className="fact-grid">
              <div className="fact">
                <dt>Platform</dt>
                <dd>{platform ? platformLabel(platform.current) : '—'}</dd>
              </div>
              <div className="fact">
                <dt>Vela version</dt>
                <dd className="mono">{health?.version || '—'}</dd>
              </div>
              <div className="fact fact-wide">
                <dt>Supported platforms</dt>
                <dd>{platform ? platform.supported.map(platformLabel).join(' · ') : '—'}</dd>
              </div>
            </dl>
            <AddToHomeScreen appName="Vela" forHub />
            <div className="phone-setup-settings">
              <Link className="btn" to="/?setup=phone">
                Set up my phone
              </Link>
              <p className="phone-note">Reopen the welcome guide and connect your phone.</p>
            </div>
          </section>

          <section className="panel" id="settings-appearance" hidden={active !== 'appearance'}>
            <div className="panel-head">
              <h2>Appearance</h2>
            </div>
            <div className="settings-row">
              <div>
                <h3>Theme</h3>
                <p>Choose a light or dark look for your dashboard.</p>
              </div>
            </div>
            <div className="settings-theme-grid" role="group" aria-label="Theme">
              {['light', 'dark'].map((t) => (
                <button
                  key={t}
                  type="button"
                  aria-pressed={theme === t}
                  className={`settings-theme${theme === t ? ' is-selected' : ''}`}
                  disabled={saving}
                  onClick={() => pickTheme(t)}
                >
                  <span className={`settings-theme-preview preview-${t}`} aria-hidden="true">
                    <span className="preview-sidebar">
                      <i />
                      <i />
                      <i />
                    </span>
                    <span className="preview-content">
                      <i />
                      <span>
                        <i />
                        <i />
                      </span>
                      <i />
                    </span>
                  </span>
                  <span className="settings-theme-label">
                    {t === 'light' ? 'Light' : 'Dark'}
                    {theme === t && <Check size={16} weight="bold" />}
                  </span>
                </button>
              ))}
            </div>
          </section>
          <div id="settings-security" hidden={active !== 'security'}>
            <SecuritySection onPendingChange={onPendingChange} onSubScreen={onSubScreen} />
          </div>

          <section className="panel" id="settings-chat" hidden={active !== 'chat'}>
            <div className="settings-row">
              <div>
                <h3>Remember chat on this device</h3>
                <p>
                  Keeps your last assistant conversation in this browser. Turning it off wipes it
                  immediately.
                </p>
              </div>
              <div className="seg" role="group" aria-label="Remember chat on this device">
                {[true, false].map((v) => (
                  <button
                    key={String(v)}
                    type="button"
                    aria-pressed={chatHistory === v}
                    disabled={!settings || saving}
                    className={`seg-opt${chatHistory === v ? ' seg-opt-active' : ''}`}
                    onClick={() => pickChatHistory(v)}
                  >
                    {v ? 'On' : 'Off'}
                  </button>
                ))}
              </div>
            </div>
          </section>

          <div hidden={active !== 'desk'}>
            <DeskSection
              settings={settings}
              onPatched={onPatched}
              onPendingChange={onPendingChange}
            />
          </div>

          <div hidden={active !== 'ai'}>
            <AiSection
              settings={settings}
              onPatched={onPatched}
              onPendingChange={onPendingChange}
            />
          </div>

          <div hidden={active !== 'notifications'}>
            <fieldset className="settings-fields" disabled={!settings}>
              <NotificationsSection
                settings={settings}
                onPatched={onPatched}
                onPendingChange={onPendingChange}
              />
            </fieldset>
          </div>

          <div hidden={active !== 'updates'}>
            <UpdatesSection onPendingChange={onPendingChange} />
          </div>

          <div hidden={active !== 'health'}>
            <HealthSection onPendingChange={onPendingChange} />
          </div>

          <div hidden={active !== 'backups'}>
            <BackupsSection onPendingChange={onPendingChange} />
            <section className="panel">
              <div className="panel-head">
                <h2>Storage</h2>
              </div>
              <dl className="fact-grid">
                <div className="fact">
                  <dt>Used by Vela</dt>
                  <dd>{engine ? formatBytes(engine.storage_bytes) : '—'}</dd>
                </div>
              </dl>
              <p className="panel-note">
                Your apps and their data stay on this computer. Removing an app releases the space
                it was using.
              </p>
            </section>
          </div>

          <section className="panel" id="settings-developer" hidden={active !== 'developer'}>
            {locked ? (
              <>
                <div className="panel-head">
                  <h2 tabIndex={-1} ref={lockedRef}>
                    Developer tools are off
                  </h2>
                </div>
                <p className="panel-note">
                  App logs, system details and the tools for developing apps are hidden in this
                  browser. Turning them on changes what you see here — it does not change any app’s
                  permissions or start anything.
                </p>
                <div className="actions">
                  <Button
                    variant="primary"
                    disabled={pending}
                    onClick={() => setDeveloperTools(true)}
                  >
                    Enable developer tools
                  </Button>
                  <Button disabled={pending} onClick={() => setActive('general')}>
                    Back to General
                  </Button>
                </div>
              </>
            ) : (
              <>
                <div className="panel-head">
                  <h2>Developer tools</h2>
                  <Link className="btn btn-small" to="/environments">
                    Open System
                  </Link>
                </div>
                <p className="panel-note">
                  App logs and per-app diagnostics live with each app, under App settings. This
                  shows the server behind them. System › Logs reads the logs Vela writes, System ›
                  Errors lists what has failed, and{' '}
                  <Link to="/environments">Create support bundle</Link> packages both into one
                  redacted file you can share.
                </p>
                <dl className="fact-grid">
                  <div className="fact">
                    <dt>Engine endpoint</dt>
                    <dd className="mono">{engine?.endpoint || '—'}</dd>
                  </div>
                  <div className="fact">
                    <dt>API base</dt>
                    <dd className="mono">/api (same origin)</dd>
                  </div>
                  <div className="fact fact-wide">
                    <dt>Data directory</dt>
                    <dd className="mono">{engine?.data_dir || '—'}</dd>
                  </div>
                  <div className="fact fact-wide">
                    <dt>App serving</dt>
                    <dd className="mono">
                      /apps/&lt;id&gt;/ (proxied inside the hub — apps never expose ports to the UI)
                    </dd>
                  </div>
                </dl>
              </>
            )}
          </section>
        </div>
        {(!compact || pending || saveError) && (
          <footer className="settings-footer">
            {saveError ? (
              <span className="inline-error" role="alert">
                {saveError}
              </span>
            ) : (
              <span role="status">
                {pending ? 'Working…' : 'Theme and chat preferences save automatically.'}
              </span>
            )}
            {/* A phone already has Back and Close in its header; a permanent
              Done footer would only take space from the form. */}
            {!compact && (
              <Button disabled={pending} onClick={onClose}>
                Done
              </Button>
            )}
          </footer>
        )}
      </div>
    </Dialog>
  );
}
