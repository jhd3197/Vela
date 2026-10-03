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
  PaperPlaneTilt,
  Plus,
  ShieldCheck,
  ArrowCircleUp,
  Stethoscope,
  Folder,
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
import DevicesSection from '../components/DevicesSection.jsx';
import SecuritySection from '../components/security/SecuritySection.jsx';
import HealthSection from '../components/HealthSection.jsx';
import UpdatesSection from '../components/UpdatesSection.jsx';
import useMediaQuery from '../hooks/useMediaQuery.js';
import { useForm } from '../hooks/useForm.js';
import { removeLocal } from '../storage.js';
import PersonaliseFields from '../desk/PersonaliseFields.jsx';
import {
  SettingRow,
  SettingsActions,
  SettingsGroup,
  SettingsNote,
  SettingsPage,
  SettingsStatus,
} from '../components/settings/SettingsKit.jsx';

// The shared compact threshold, named in `_breakpoints.scss`. Below it Settings
// is a screen inside the app; above it, a window on the desk.
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

  const current = settings?.chat_model ?? ai?.chat_model;

  return (
    <SettingsGroup
      id="settings-ai"
      title="Local AI"
      description="Vela talks to the AI runtime on this computer. Nothing is sent anywhere else."
      aside={
        <>
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
        </>
      }
      footer={
        <>
          {ai?.hint && <SettingsNote>{ai.hint}</SettingsNote>}
          <SettingsStatus tone="error">{saveError}</SettingsStatus>
        </>
      }
    >
      {!ai && (
        <SettingRow
          title={failed ? 'Status unavailable' : 'Checking…'}
          description={
            failed
              ? "Couldn't reach the status endpoint."
              : 'Checking the local AI runtime on this computer.'
          }
        />
      )}
      {ai && <SettingRow title="Endpoint" value={ai.url || '—'} mono />}
      {ai && (
        <SettingRow
          title="Chat model"
          titleId="ai-model-label"
          stacked={ai.reachable && ai.models?.length > 0}
          description={
            !ai.reachable
              ? 'Start the runtime to choose a model.'
              : ai.models?.length === 0
                ? 'No models found. Pull one first, for example ollama pull qwen3:8b.'
                : ai.model_available === false
                  ? "The selected model isn't installed yet. Pick one below or pull it first."
                  : 'The model Ask and your bots use.'
          }
          value={ai.reachable && ai.models?.length > 0 ? undefined : current || '—'}
          mono
          control={
            ai.reachable && ai.models?.length > 0 ? (
              <div className="chip-row" role="group" aria-labelledby="ai-model-label">
                {ai.models.map((m) => (
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
                ))}
              </div>
            ) : null
          }
        />
      )}
    </SettingsGroup>
  );
}

// ------------------------------------------------------------- Notifications

const NTFY_EVENTS = [
  {
    key: 'digest',
    label: 'Hub digest',
    description: 'Once a day after 9:00: which apps are running and how much space Vela uses.',
  },
  {
    key: 'status_alerts',
    label: 'Status alerts',
    description: 'When an app that was running stops.',
  },
];

function NotificationsSection({ settings, onPatched, onPendingChange }) {
  const { pushToast } = useApps();
  const ntfy = settings?.ntfy_config || {};
  const events = ntfy.events || {};
  // Save and Send test are the same submit with one extra step, so the mode
  // rides in a ref rather than forking the form.
  const sendTestRef = useRef(false);
  const [patching, setPatching] = useState(false);
  const [note, setNote] = useState(null); // { kind: 'ok' | 'err', text }

  const form = useForm({
    initialValues: { server: '', topic: '', user: '', pass: '' },
    onSubmit: async (values) => {
      const sendTest = sendTestRef.current;
      setNote({ kind: 'ok', text: sendTest ? 'Saving and sending…' : 'Saving…' });
      const patch = {
        server: values.server.trim(),
        topic: values.topic.trim(),
        user: values.user.trim(),
        // The password is write-only: only send it when the user typed one.
        ...(values.pass ? { pass: values.pass } : {}),
      };
      try {
        await api.updateSettings({ ntfy_config: patch });
      } catch (error) {
        // useForm puts the message under the form; this line is the status.
        setNote(null);
        throw error;
      }
      onPatched({
        ntfy_config: { ...patch, passConfigured: ntfy.passConfigured || !!values.pass },
      });
      // Saved values are the new baseline, and the password field empties: it
      // is write-only, so what is stored is never what is shown.
      form.reset({ ...values, pass: '' });
      if (!sendTest) {
        setNote({ kind: 'ok', text: 'Notification settings saved.' });
        return;
      }
      const receipt = await api.testNotify();
      setNote({
        kind: 'ok',
        text: `Accepted by ntfy at ${new Date(receipt.accepted_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}. Check your phone — server acceptance doesn't confirm delivery.`,
      });
    },
  });

  const sending = form.submitting || patching;
  useEffect(() => {
    onPendingChange('notifications', sending);
    return () => onPendingChange('notifications', false);
  }, [sending, onPendingChange]);

  const { reset, dirty } = form;
  useEffect(() => {
    // The record arrives after the first render, and again on every refresh.
    // Adopting it over unsaved typing is how a settings form loses work.
    if (settings && !dirty) {
      reset({
        server: ntfy.server || '',
        topic: ntfy.topic || '',
        user: ntfy.user || '',
        pass: '',
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings]);

  const change = (name) => (event) => {
    form.setValue(name, event.target.value);
    setNote({ kind: 'ok', text: 'Unsaved changes.' });
  };

  const patchNtfy = (patch, successText, localPatch) => {
    if (sending) return;
    setPatching(true);
    setNote(null);
    api
      .updateSettings({ ntfy_config: patch })
      .then(() => {
        onPatched({ ntfy_config: localPatch || patch });
        if (successText) pushToast(successText, 'success');
        setNote({
          kind: 'ok',
          text:
            successText ||
            (form.dirty
              ? 'Event preference saved. Connection details have unsaved changes.'
              : 'Notification preference saved.'),
        });
      })
      .catch((err) =>
        setNote({ kind: 'err', text: err.message || 'Could not save notification settings.' }),
      )
      .finally(() => setPatching(false));
  };

  const save = (sendTest) => {
    sendTestRef.current = sendTest;
    return form.handleSubmit();
  };

  // A failed submit says so under the form; everything else is a status line.
  const message = form.formError ? { kind: 'err', text: form.formError } : note;
  const live = !!(form.values.server.trim() && form.values.topic.trim());

  return (
    <SettingsPage id="settings-notifications">
      <SettingsGroup
        title="Notifications"
        aside={
          <StatusPill state={live ? 'ok' : 'idle'} text={live ? 'Configured' : 'Not set up'} />
        }
        description={
          form.values.topic.trim() ? (
            <>
              Subscribe to{' '}
              <b className="mono">
                {form.values.server.trim() || 'https://ntfy.sh'}/{form.values.topic.trim()}
              </b>{' '}
              in the ntfy app on your phone.
            </>
          ) : (
            'Push events to your phone through any ntfy server. Subscribe to the topic in the ntfy app.'
          )
        }
        footer={
          <>
            <SettingsActions>
              <Button disabled={sending || !form.dirty} onClick={() => save(false)}>
                Save
              </Button>
              <Button variant="primary" disabled={sending || !live} onClick={() => save(true)}>
                <PaperPlaneTilt size={14} />
                {sending ? 'Working…' : 'Send test'}
              </Button>
            </SettingsActions>
            {message && (
              <SettingsStatus tone={message.kind === 'err' ? 'error' : 'info'}>
                {message.text}
              </SettingsStatus>
            )}
          </>
        }
      >
        <SettingRow
          title="Server"
          htmlFor="ntfy-server"
          description="Leave empty for ntfy.sh."
          control={
            <input
              id="ntfy-server"
              value={form.values.server}
              disabled={sending}
              onChange={change('server')}
              placeholder="https://ntfy.sh"
              autoComplete="off"
            />
          }
        />
        <SettingRow
          title="Topic"
          htmlFor="ntfy-topic"
          description="Anyone who knows the topic can read it, so make it hard to guess."
          control={
            <input
              id="ntfy-topic"
              value={form.values.topic}
              disabled={sending}
              onChange={change('topic')}
              placeholder="my-vela-alerts"
              autoComplete="off"
            />
          }
        />
        <SettingRow
          title="User"
          htmlFor="ntfy-user"
          description="Only if your ntfy server asks for one."
          control={
            <input
              id="ntfy-user"
              value={form.values.user}
              disabled={sending}
              onChange={change('user')}
              autoComplete="off"
            />
          }
        />
        <SettingRow
          title="Password"
          htmlFor="ntfy-pass"
          description={
            ntfy.passConfigured ? 'Saved. Type a new one to replace it.' : 'Kept on this server.'
          }
          control={
            <span className="set-field-pair">
              <input
                id="ntfy-pass"
                type="password"
                value={form.values.pass}
                disabled={sending}
                onChange={change('pass')}
                placeholder={ntfy.passConfigured ? 'Configured' : '••••••'}
                autoComplete="new-password"
              />
              {ntfy.passConfigured && (
                <Button
                  type="button"
                  variant="ghost"
                  disabled={sending}
                  onClick={() =>
                    patchNtfy({ pass: '' }, 'ntfy password cleared.', { passConfigured: false })
                  }
                >
                  Clear
                </Button>
              )}
            </span>
          }
        />
      </SettingsGroup>

      <SettingsGroup title="What to send" description="Each event saves as soon as you switch it.">
        {NTFY_EVENTS.map(({ key, label, description }) => (
          <SettingRow
            key={key}
            title={label}
            description={description}
            toggle={{
              checked: Boolean(events[key]),
              disabled: sending,
              onChange: () => patchNtfy({ events: { [key]: !events[key] } }),
            }}
          />
        ))}
      </SettingsGroup>
    </SettingsPage>
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
      <SettingsPage id="settings-backups">
        <SettingsGroup
          title="Backups"
          description="A backup copies your settings, which apps are installed and what they saved. Logs and wallpapers are not included."
          aside={
            <Button size="small" variant="primary" disabled={busy} onClick={create}>
              <Plus size={14} />
              {creating ? 'Creating…' : 'Create backup'}
            </Button>
          }
          footer={
            note && (
              <SettingsStatus tone={note.kind === 'err' ? 'error' : 'ok'}>
                {note.text}
              </SettingsStatus>
            )
          }
        >
          {/* What is protected right now, before anything about how. */}
          <SettingRow
            title="Last backup"
            value={stats?.lastSuccessAt ? relTime(stats.lastSuccessAt) : 'Never'}
          />
          <SettingRow
            title="Next backup"
            value={
              schedule?.nextRunAt
                ? new Date(schedule.nextRunAt).toLocaleString(undefined, {
                    weekday: 'short',
                    hour: '2-digit',
                    minute: '2-digit',
                  })
                : 'Not scheduled'
            }
          />
          <SettingRow
            title="Kept"
            value={stats ? `${stats.count} · ${formatBytes(stats.totalSize)}` : '—'}
          />
        </SettingsGroup>

        {/* The schedule. Off by default: Vela does not start writing copies on
            a timer until someone asks it to. */}
        <SettingsGroup title="Schedule">
          <SettingRow
            title="Back up automatically"
            description={`Once a day, at the time you choose${
              schedule?.timezone ? `, by this computer's clock (${schedule.timezone})` : ''
            }. A run missed because Vela was off is not made up later.`}
            toggle={{
              checked: Boolean(schedule?.enabled),
              disabled: busy || !schedule,
              onChange: () => saveSchedule({ enabled: !schedule.enabled }),
            }}
          />
          {schedule?.enabled && (
            <SettingRow
              title="At"
              htmlFor="backup-time"
              control={
                <input
                  id="backup-time"
                  type="time"
                  value={schedule.time}
                  disabled={busy}
                  onChange={(event) => saveSchedule({ time: event.target.value })}
                />
              }
            />
          )}
          {schedule?.enabled && (
            <SettingRow
              title="Backups to keep"
              htmlFor="backup-keep"
              description="Older ones are removed automatically."
              control={
                <input
                  id="backup-keep"
                  type="number"
                  min="1"
                  max="100"
                  value={schedule.keep}
                  disabled={busy}
                  onChange={(event) => saveSchedule({ keep: Number(event.target.value) })}
                />
              }
            />
          )}
        </SettingsGroup>

        <SettingsGroup
          title="Saved backups"
          footer={
            <SettingsNote>
              Verify runs a restore drill in an isolated folder. Your live data is never touched by
              it.
            </SettingsNote>
          }
        >
          {backups === null && <SettingRow title="Checking…" />}
          {backups !== null && backups.length === 0 && (
            <SettingRow title="No backups yet" description="Create the first snapshot above." />
          )}
          {backups?.map((b) => {
            const result = results[b.name];
            return (
              <SettingRow
                key={b.name}
                className="backup-row"
                lead={
                  <ShieldCheck
                    size={18}
                    className={result ? (result.ok ? 'backup-ok' : 'backup-bad') : undefined}
                  />
                }
                title={<span className="mono">{b.name}</span>}
                description={
                  <>
                    {relTime(b.created_at)} · {formatBytes(b.size)}
                    {b.safety ? ' · taken before a restore' : ''}
                    {result && (
                      <span className={result.ok ? 'backup-note-ok' : 'backup-note-bad'}>
                        {' '}
                        — {result.text}
                      </span>
                    )}
                  </>
                }
                control={
                  <>
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
                  </>
                }
              />
            );
          })}
        </SettingsGroup>
      </SettingsPage>

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
    <SettingsPage id="settings-desk">
      <SettingsGroup
        title="Volumes"
        description="Each folder here gets a Volume widget on your desk. Vela only reports how full it is. It does not read what is inside."
      >
        {volumes.length === 0 && (
          <SettingRow title="No volumes yet" description="Add a folder below to show it." />
        )}
        {volumes.map((volume) => (
          <SettingRow
            key={volume.path}
            title={volume.label || volume.path}
            description={<span className="mono">{volume.path}</span>}
            control={
              <Button
                size="icon"
                variant="ghost"
                aria-label={`Remove ${volume.label || volume.path}`}
                disabled={saving}
                onClick={() => save(volumes.filter((entry) => entry.path !== volume.path))}
              >
                <Trash size={16} aria-hidden="true" />
              </Button>
            }
          />
        ))}
      </SettingsGroup>

      <form onSubmit={add}>
        <SettingsGroup
          title="Add a volume"
          footer={
            <>
              <SettingsActions>
                <Button type="submit" pending={saving} disabled={!draft.path.trim()}>
                  Add volume
                </Button>
              </SettingsActions>
              <SettingsStatus tone="error">{note}</SettingsStatus>
            </>
          }
        >
          <SettingRow
            title="Folder"
            htmlFor="desk-volume-path"
            description="A folder on this computer."
            control={
              <input
                id="desk-volume-path"
                value={draft.path}
                disabled={saving}
                placeholder="D:\Media"
                autoComplete="off"
                onChange={(event) => setDraft((prev) => ({ ...prev, path: event.target.value }))}
              />
            }
          />
          <SettingRow
            title="Display name"
            htmlFor="desk-volume-label"
            description="Optional. The folder's name is used otherwise."
            control={
              <input
                id="desk-volume-label"
                value={draft.label}
                disabled={saving}
                placeholder="Media"
                autoComplete="off"
                onChange={(event) => setDraft((prev) => ({ ...prev, label: event.target.value }))}
              />
            }
          />
        </SettingsGroup>
      </form>
    </SettingsPage>
  );
}

// Files: the folders the Files app may show. This is the only place a share is
// named, and the server refuses a path that is not a folder rather than storing
// it and failing inside the app later. Vela's own data folder is refused
// outright: browsing it would be a way to delete every app's data at once.
function FilesSection({ settings, onPatched, onPendingChange }) {
  const shares = settings?.files?.shares || [];
  const [draft, setDraft] = useState({ path: '', label: '', writable: true });
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState('');

  useEffect(() => {
    onPendingChange('files', saving);
    return () => onPendingChange('files', false);
  }, [saving, onPendingChange]);

  const save = (next) => {
    setSaving(true);
    setNote('');
    return api
      .updateSettings({ files: { shares: next } })
      .then(() => {
        onPatched({ files: { ...(settings?.files || {}), shares: next } });
        setDraft({ path: '', label: '', writable: true });
      })
      .catch((error) => setNote(error.message || 'Could not save that share.'))
      .finally(() => setSaving(false));
  };

  const add = (event) => {
    event.preventDefault();
    const path = draft.path.trim();
    if (!path) return;
    const label = draft.label.trim();
    // An id the user never has to think about, from the label or the folder.
    const base = (
      label ||
      path
        .replace(/[\\/]+$/, '')
        .split(/[\\/]/)
        .pop() ||
      'share'
    )
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 40);
    let id = base || 'share';
    let n = 2;
    while (shares.some((entry) => entry.id === id)) id = `${base}-${n++}`;
    save([...shares, { id, path, label, writable: draft.writable }]);
  };

  return (
    <SettingsPage id="settings-files">
      <SettingsGroup
        title="Shared folders"
        description="The Files app can show these folders and nothing outside them. Vela's own data folder cannot be added. Deleting from Files moves things to a trash that is cleared after 30 days."
      >
        {shares.length === 0 && (
          <SettingRow
            title="No shared folders yet"
            description="Add a folder below to browse it."
          />
        )}
        {shares.map((share) => (
          <SettingRow
            key={share.id}
            title={`${share.label || share.path}${share.writable ? '' : ' · read-only'}`}
            description={<span className="mono">{share.path}</span>}
            control={
              <Button
                size="icon"
                variant="ghost"
                aria-label={`Remove ${share.label || share.path}`}
                disabled={saving}
                onClick={() => save(shares.filter((entry) => entry.id !== share.id))}
              >
                <Trash size={16} aria-hidden="true" />
              </Button>
            }
          />
        ))}
      </SettingsGroup>

      <form onSubmit={add}>
        <SettingsGroup
          title="Add a folder"
          footer={
            <>
              <SettingsActions>
                <Button type="submit" pending={saving} disabled={!draft.path.trim()}>
                  Add share
                </Button>
              </SettingsActions>
              <SettingsStatus tone="error">{note}</SettingsStatus>
            </>
          }
        >
          <SettingRow
            title="Folder"
            htmlFor="files-share-path"
            description="A folder on this computer."
            control={
              <input
                id="files-share-path"
                value={draft.path}
                disabled={saving}
                placeholder="D:\Documents"
                autoComplete="off"
                onChange={(event) => setDraft((prev) => ({ ...prev, path: event.target.value }))}
              />
            }
          />
          <SettingRow
            title="Display name"
            htmlFor="files-share-label"
            description="Optional. The folder's name is used otherwise."
            control={
              <input
                id="files-share-label"
                value={draft.label}
                disabled={saving}
                placeholder="Documents"
                autoComplete="off"
                onChange={(event) => setDraft((prev) => ({ ...prev, label: event.target.value }))}
              />
            }
          />
          <SettingRow
            title="Let Vela change this folder"
            description="Off means you can look and download, but not add, rename or delete."
            toggle={{
              checked: draft.writable,
              disabled: saving,
              onChange: (writable) => setDraft((prev) => ({ ...prev, writable })),
            }}
          />
        </SettingsGroup>
      </form>
    </SettingsPage>
  );
}

// --------------------------------------------------------------------- page

const SECTIONS = [
  {
    id: 'general',
    label: 'General',
    icon: Info,
    description: 'Your server, your devices, and how much Vela shows.',
    keywords:
      'version platform phone install home screen about developer tools logs system android app pair devices tv',
  },
  {
    id: 'desk',
    label: 'Desk',
    icon: SquaresFour,
    description: 'What your home board is allowed to show.',
    keywords: 'desk widgets volumes drive disk wallpaper home board',
  },
  {
    id: 'files',
    label: 'Files',
    icon: Folder,
    description: 'The folders the Files app may show.',
    keywords: 'files shares folders browse downloads trash upload share read-only',
  },
  {
    id: 'appearance',
    label: 'Appearance',
    icon: Palette,
    description: 'Light or dark, the style, and how this desktop looks.',
    keywords: 'theme light dark style wallpaper background personalise dim labels weather',
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

/**
 * Settings, as a screen on a phone or as a window on the desk.
 *
 * `windowed` draws it inside a desk window: the window's own title bar closes
 * it, so there is no close button or Done, and Escape does not dismiss it any
 * more than it would dismiss any other window. `sectionRequest` (`{ id, n }`)
 * is how a window that is already open is sent to a section: each new `n` is a
 * new request, so asking twice for the same section still switches to it.
 */
export default function Settings({
  initialSection = 'appearance',
  explicit = false,
  onClose,
  windowed = false,
  sectionRequest = null,
}) {
  const { platform, pushToast } = useApps();
  const { engine } = useEngine();
  const developer = useDeveloperTools();
  const narrow = useMediaQuery(COMPACT);
  const compact = narrow && !windowed;
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
  // The two names, edited as a draft and saved together: typing a name one
  // letter at a time should not write the file eight times, and the avatar
  // should not change letter while the name is half typed.
  const [identityDraft, setIdentityDraft] = useState({ displayName: '', serverName: '' });
  const [identitySaved, setIdentitySaved] = useState({ displayName: '', serverName: '' });
  const identityChanged =
    identityDraft.displayName !== identitySaved.displayName ||
    identityDraft.serverName !== identitySaved.serverName;
  const onPendingChange = useCallback((section, pending) => {
    setPendingSections((previous) => ({ ...previous, [section]: pending }));
  }, []);
  // A section reports the way out of its own deeper screen, or null when it
  // has none. Back and Escape use it before stepping up to the category list.
  const onSubScreen = useCallback((back) => {
    backRef.current = back;
    setSubScreen(Boolean(back));
  }, []);
  const saveIdentity = async () => {
    setSaving(true);
    setSaveError('');
    try {
      const saved = await api.updateSettings({ identity: identityDraft });
      const identity = {
        displayName: saved.identity?.displayName || '',
        serverName: saved.identity?.serverName || '',
      };
      setSettings(saved);
      setIdentityDraft(identity);
      setIdentitySaved(identity);
      // The rail draws the avatar from this setting, so it is told rather than
      // left with the old letter until something else makes it re-read.
      dispatchEvent(new Event('vela:identity-changed'));
      pushToast('Saved.', 'success');
    } catch (error) {
      setSaveError(error.message || 'Could not save those names.');
    } finally {
      setSaving(false);
    }
  };

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
        const identity = {
          displayName: s.identity?.displayName || '',
          serverName: s.identity?.serverName || '',
        };
        setIdentityDraft(identity);
        setIdentitySaved(identity);
        if (s.theme === 'dark' || s.theme === 'light') setThemeState(s.theme);
      })
      .catch(() => setLoadError(true));
  }, []);

  useEffect(() => {
    contentRef.current?.scrollTo(0, 0);
  }, [active, listed]);

  // A failed save is reported beside the setting that failed; it does not
  // follow the reader into another section.
  useEffect(() => {
    setSaveError('');
  }, [active]);

  // Asked for a section while already open: go there, as a settings window on
  // any desktop OS does when another part of the system links into it.
  const requestId = sectionRequest?.id;
  const requestN = sectionRequest?.n;
  useEffect(() => {
    if (!requestN || !requestId) return;
    setActive(resolveSection(requestId));
    setListed(false);
    setQuery('');
  }, [requestId, requestN]);

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
      // Private mode etc. — there is then nothing left to wipe.
      removeLocal('vela-chat');
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

  const body = (
    <>
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
        {/* The list beside the section already says where you are, so a
            wide screen names the section only for a screen reader. A phone
            shows one screen at a time and needs the title and the way back. */}
        {compact ? (
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
            {!windowed && (
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
            )}
          </header>
        ) : (
          <h2 className="sr-only">{section.label}</h2>
        )}
        <div className="settings-content" ref={contentRef}>
          {loadError && (
            <SettingsStatus tone="error">
              Could not load settings. Close this window and try again.
            </SettingsStatus>
          )}

          <SettingsPage id="settings-general" hidden={active !== 'general'}>
            {/* What this server is called and who it belongs to. Both are
                labels: naming the server here changes nothing about the
                network or the address Vela answers on. */}
            <SettingsGroup
              title="Your server"
              description="Vela greets you by name and shows the server's name on the desk."
              footer={
                <>
                  <SettingsActions>
                    <Button disabled={pending || !identityChanged} onClick={saveIdentity}>
                      Save names
                    </Button>
                  </SettingsActions>
                  <SettingsStatus tone="error">{saveError}</SettingsStatus>
                </>
              }
            >
              <SettingRow
                title="Your name"
                htmlFor="identity-display"
                control={
                  <input
                    id="identity-display"
                    type="text"
                    maxLength={60}
                    value={identityDraft.displayName}
                    disabled={pending}
                    placeholder="Marco"
                    onChange={(event) =>
                      setIdentityDraft((draft) => ({
                        ...draft,
                        displayName: event.target.value,
                      }))
                    }
                  />
                }
              />
              <SettingRow
                title="Server name"
                htmlFor="identity-server"
                description="A label, not the address this computer answers on."
                control={
                  <input
                    id="identity-server"
                    type="text"
                    maxLength={60}
                    value={identityDraft.serverName}
                    disabled={pending}
                    placeholder="vela.marco.house"
                    onChange={(event) =>
                      setIdentityDraft((draft) => ({
                        ...draft,
                        serverName: event.target.value,
                      }))
                    }
                  />
                }
              />
            </SettingsGroup>

            <SettingsGroup title="Phone and home screen">
              <SettingRow
                title="Your phone"
                description="Reopen the welcome guide and connect your phone."
                control={
                  <Link className="btn" to="/?setup=phone">
                    Set up my phone
                  </Link>
                }
              />
              <AddToHomeScreen appName="Vela" forHub row />
            </SettingsGroup>

            <DevicesSection />

            <SettingsGroup title="Developer">
              <SettingRow
                title="Show developer tools"
                description={
                  developerToolsPersist()
                    ? 'Show app logs, system details, and tools for developing apps in this browser.'
                    : 'Show app logs, system details, and tools for developing apps in this browser. This browser is not storing preferences, so the choice lasts until you close the tab.'
                }
                toggle={{
                  checked: developer,
                  disabled: pending,
                  onChange: setDeveloperTools,
                }}
              />
            </SettingsGroup>

            <SettingsGroup title="About">
              <SettingRow title="Vela version" value={health?.version || '—'} mono />
              <SettingRow
                title="Platform"
                value={platform ? platformLabel(platform.current) : '—'}
              />
              <SettingRow
                title="Supported platforms"
                value={platform ? platform.supported.map(platformLabel).join(' · ') : '—'}
              />
            </SettingsGroup>
          </SettingsPage>

          <SettingsPage id="settings-appearance" hidden={active !== 'appearance'}>
            <SettingsGroup
              title="Light or dark"
              footer={<SettingsStatus tone="error">{saveError}</SettingsStatus>}
            >
              <SettingRow
                title="Theme"
                description="Choose a light or dark look for your dashboard."
                stacked
              >
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
                      <span
                        className={`settings-theme-preview preview-${t}`}
                        data-theme={t}
                        aria-hidden="true"
                      >
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
              </SettingRow>
            </SettingsGroup>
            <PersonaliseFields
              weather={settings?.desk?.weather}
              onWeatherChange={(weather) => onPatched({ desk: { weather } })}
            />
          </SettingsPage>

          <div id="settings-security" hidden={active !== 'security'}>
            <SecuritySection onPendingChange={onPendingChange} onSubScreen={onSubScreen} />
          </div>

          <SettingsPage id="settings-chat" hidden={active !== 'chat'}>
            <SettingsGroup
              title="This device"
              footer={<SettingsStatus tone="error">{saveError}</SettingsStatus>}
            >
              <SettingRow
                title="Remember chat on this device"
                description="Keeps your last assistant conversation in this browser. Turning it off wipes it immediately."
                toggle={{
                  checked: chatHistory,
                  disabled: !settings || saving,
                  onChange: pickChatHistory,
                }}
              />
            </SettingsGroup>
          </SettingsPage>

          <div hidden={active !== 'desk'}>
            <DeskSection
              settings={settings}
              onPatched={onPatched}
              onPendingChange={onPendingChange}
            />
          </div>

          <div hidden={active !== 'files'}>
            <FilesSection
              settings={settings}
              onPatched={onPatched}
              onPendingChange={onPendingChange}
            />
          </div>

          <SettingsPage hidden={active !== 'ai'}>
            <AiSection
              settings={settings}
              onPatched={onPatched}
              onPendingChange={onPendingChange}
            />
          </SettingsPage>

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

          <SettingsPage hidden={active !== 'backups'}>
            <BackupsSection onPendingChange={onPendingChange} />
            <SettingsGroup
              title="Storage"
              footer={
                <SettingsNote>
                  Your apps and their data stay on this computer. Removing an app releases the space
                  it was using.
                </SettingsNote>
              }
            >
              <SettingRow
                title="Used by Vela"
                value={engine ? formatBytes(engine.storage_bytes) : '—'}
              />
            </SettingsGroup>
          </SettingsPage>

          <SettingsPage id="settings-developer" hidden={active !== 'developer'}>
            {locked ? (
              <SettingsGroup
                title="Developer tools are off"
                headingRef={lockedRef}
                description="App logs, system details and the tools for developing apps are hidden in this browser. Turning them on changes what you see here. It does not change any app's permissions or start anything."
                footer={
                  <SettingsActions>
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
                  </SettingsActions>
                }
              />
            ) : (
              <SettingsGroup
                title="Developer tools"
                aside={
                  <Link className="btn btn-small" to="/environments">
                    Open System
                  </Link>
                }
                description={
                  <>
                    App logs and per-app diagnostics live with each app, under App settings. This
                    shows the server behind them. System › Logs reads the logs Vela writes, System ›
                    Errors lists what has failed, and{' '}
                    <Link to="/environments">Create support bundle</Link> packages both into one
                    redacted file you can share.
                  </>
                }
              >
                <SettingRow title="Engine endpoint" value={engine?.endpoint || '—'} mono />
                <SettingRow title="API base" value="/api (same origin)" mono />
                <SettingRow title="Data directory" value={engine?.data_dir || '—'} mono />
                <SettingRow
                  title="App serving"
                  description="Proxied inside the hub. Apps never expose ports to the UI."
                  value="/apps/<id>/"
                  mono
                />
              </SettingsGroup>
            )}
          </SettingsPage>
        </div>
        {(!compact || pending) && (
          <footer className="settings-footer">
            <span role="status">
              {pending ? 'Working…' : 'Appearance and chat preferences save automatically.'}
            </span>
            {/* A phone already has Back and Close in its header; a permanent
              Done footer would only take space from the form. */}
            {!compact && !windowed && (
              <Button disabled={pending} onClick={onClose}>
                Done
              </Button>
            )}
          </footer>
        )}
      </div>
    </>
  );

  if (windowed) {
    return (
      <div
        className="settings-dialog settings-window"
        aria-labelledby="settings-title"
        role="region"
      >
        {body}
      </div>
    );
  }

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
      {body}
    </Dialog>
  );
}
