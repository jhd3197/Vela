import Button from '../components/ui/Button.jsx';
import FormField from '../components/ui/FormField.jsx';
import PageHeader from '../components/ui/PageHeader.jsx';
import { useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import {
  ArrowsClockwise,
  Bell,
  ChartBar,
  PaperPlaneTilt,
  Plus,
  ShieldCheck,
} from '@phosphor-icons/react';
import { api, formatBytes, platformLabel, relTime } from '../api.js';
import { useApps, useEngine } from '../store.jsx';
import { getTheme, setTheme } from '../theme.js';
import AddToHomeScreen from '../components/AddToHomeScreen.jsx';

// Settings backed by the hub's settings API: theme, chat retention, ntfy
// notifications, local AI model, and backups. Read-only fact grids report
// what the backend actually exposes.

function StatusPill({ state, text }) {
  return (
    <span className={`pill pill-${state}`}>
      <span className={`status-dot${state === 'ok' ? ' status-dot-ok' : state === 'bad' ? ' status-dot-bad' : ''}`} />
      {text}
    </span>
  );
}

// ---------------------------------------------------------------- Local AI

function AiSection({ settings, onPatched }) {
  const { pushToast } = useApps();
  const [ai, setAi] = useState(null);
  const [failed, setFailed] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pickModel = (model) => {
    if (model === settings?.chat_model) return;
    api
      .updateSettings({ chat_model: model })
      .then(() => onPatched({ chat_model: model }))
      .catch((err) => pushToast(err.message || 'Could not save the model.'));
  };

  return (
    <section className="panel" id="settings-ai">
      <div className="panel-head">
        <h2>Local AI</h2>
        <span className="panel-head-side">
          {ai && <StatusPill state={ai.reachable ? 'ok' : 'bad'} text={ai.reachable ? 'Connected' : 'Offline'} />}
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
                    onClick={() => pickModel(m)}
                  >
                    {m}
                  </button>
                );
              })}
            </div>
          )}
          {ai.reachable && ai.models?.length === 0 && (
            <p className="panel-note">No models found — pull one first, e.g. <code className="mono">ollama pull qwen3:8b</code>.</p>
          )}
          {ai.model_available === false && (
            <p className="panel-note" style={{ marginTop: 8 }}>
              The selected model isn't installed yet — pick one above or pull it first.
            </p>
          )}
          {ai.hint && <p className="panel-note" style={{ marginTop: 8 }}>{ai.hint}</p>}
        </>
      )}
      {!ai && !failed && <p className="panel-note">Checking the local AI runtime…</p>}
      {failed && !ai && <p className="panel-note">Couldn't reach the status endpoint.</p>}
    </section>
  );
}

// ------------------------------------------------------------- Notifications

const NTFY_EVENTS = [
  { key: 'digest', label: 'Hub digest', icon: ChartBar },
  { key: 'status_alerts', label: 'Status alerts', icon: Bell },
];

function NotificationsSection({ settings, onPatched }) {
  const { pushToast } = useApps();
  const ntfy = settings?.ntfy_config || {};
  const events = ntfy.events || {};
  const [form, setForm] = useState({ server: '', topic: '', user: '', pass: '' });
  const [sending, setSending] = useState(false);
  const [note, setNote] = useState(null); // { kind: 'ok' | 'err', text }
  const dirty = useRef(false);

  useEffect(() => {
    if (settings && !dirty.current) {
      setForm({ server: ntfy.server || '', topic: ntfy.topic || '', user: ntfy.user || '', pass: '' });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings]);

  const change = (patch) => {
    setForm((f) => ({ ...f, ...patch }));
    dirty.current = true;
    setNote({ kind: 'ok', text: 'Unsaved changes.' });
  };

  const patchNtfy = (patch, successText, localPatch) =>
    api
      .updateSettings({ ntfy_config: patch })
      .then(() => {
        onPatched({ ntfy_config: localPatch || patch });
        if (successText) pushToast(successText, 'success');
      })
      .catch((err) => pushToast(err.message || 'Could not save notification settings.'));

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
        <StatusPill
          state={live ? 'ok' : 'idle'}
          text={live ? 'Configured' : 'Not set up'}
        />
      </div>
      <p className="panel-note" style={{ marginBottom: 14 }}>
        {form.topic.trim() ? (
          <>
            Subscribe to <b className="mono">{form.server.trim() || 'https://ntfy.sh'}/{form.topic.trim()}</b> in the ntfy app on your phone.
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
                size="small" variant="ghost"
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
      <div className="chip-row" role="group" aria-label="Notification events" style={{ marginTop: 14 }}>
        {NTFY_EVENTS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            className={`chip${events[key] ? ' chip-active' : ''}`}
            aria-pressed={!!events[key]}
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
        <Button size="small" variant="primary" disabled={sending || !live} onClick={() => save(true)}>
          <PaperPlaneTilt size={14} />
          {sending ? 'Working…' : 'Send test'}
        </Button>
        {note && (
          <span className={note.kind === 'err' ? 'inline-error' : 'saved-note'} role={note.kind === 'err' ? 'alert' : 'status'}>
            {note.text}
          </span>
        )}
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ Backups

function BackupsSection() {
  const { pushToast } = useApps();
  const [backups, setBackups] = useState(null);
  const [creating, setCreating] = useState(false);
  const [verifying, setVerifying] = useState(null); // name being verified
  const [results, setResults] = useState({}); // name -> { ok, text }

  const refresh = () => {
    api
      .getBackups()
      .then((data) => setBackups(data.backups || []))
      .catch(() => setBackups([]));
  };

  useEffect(() => {
    refresh();
  }, []);

  const create = () => {
    setCreating(true);
    api
      .createBackup()
      .then((b) => {
        pushToast(`Backup ${b.name} created (${formatBytes(b.size)}).`, 'success');
        refresh();
      })
      .catch((err) => pushToast(err.message || 'Backup failed.'))
      .finally(() => setCreating(false));
  };

  const verify = (name) => {
    setVerifying(name);
    api
      .verifyBackup(name)
      .then((r) => {
        const manifests = r.manifests?.length ?? 0;
        setResults((prev) => ({
          ...prev,
          [name]: r.ok
            ? { ok: true, text: `Restore drill passed — ${r.files} file${r.files === 1 ? '' : 's'}, ${manifests} app manifest${manifests === 1 ? '' : 's'} checked.` }
            : { ok: false, text: 'Restore drill failed — this backup may not restore cleanly.' },
        }));
      })
      .catch((err) =>
        setResults((prev) => ({ ...prev, [name]: { ok: false, text: err.message || 'Verify failed.' } })),
      )
      .finally(() => setVerifying(null));
  };

  return (
    <section className="panel" id="settings-backups">
      <div className="panel-head">
        <h2>Backups</h2>
        <Button size="small" variant="primary" disabled={creating} onClick={create}>
          <Plus size={14} />
          {creating ? 'Creating…' : 'Create backup'}
        </Button>
      </div>
      <p className="panel-note" style={{ marginBottom: 14 }}>
        Snapshots of hub state and installed app data. Verify runs a restore drill in an isolated
        folder — live data is never touched.
      </p>
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
                <ShieldCheck size={16} className={result ? (result.ok ? 'backup-ok' : 'backup-bad') : undefined} />
                <span className="backup-main">
                  <span className="mini-list-name mono">{b.name}</span>
                  <span className="backup-meta">
                    {relTime(b.created_at)} · {formatBytes(b.size)}
                    {result && (
                      <span className={result.ok ? 'backup-note-ok' : 'backup-note-bad'}> — {result.text}</span>
                    )}
                  </span>
                </span>
                <Button
                  size="small"
                  disabled={verifying === b.name}
                  onClick={() => verify(b.name)}
                >
                  {verifying === b.name ? 'Verifying…' : 'Verify'}
                </Button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

// --------------------------------------------------------------------- page

export default function Settings() {
  const { platform, pushToast } = useApps();
  const { engine } = useEngine();
  const location = useLocation();
  const [health, setHealth] = useState(null);
  const [settings, setSettings] = useState(null);
  const [theme, setThemeState] = useState(getTheme);

  useEffect(() => {
    api.health().then(setHealth).catch(() => {});
    api
      .getSettings()
      .then((s) => {
        setSettings(s);
        if (s.theme === 'dark' || s.theme === 'light') setThemeState(s.theme);
      })
      .catch(() => setSettings({}));
  }, []);

  // Deep links (/settings#notifications etc.) scroll to their panel.
  useEffect(() => {
    if (!location.hash) return;
    const el = document.getElementById(`settings-${location.hash.slice(1)}`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [location.hash, settings]);

  // Merge a successful PATCH into local settings state (deep for nested dicts).
  const onPatched = (patch) => {
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
    setTheme(next); // instant local apply (also updates the localStorage fallback)
    setThemeState(next);
    api
      .updateSettings({ theme: next })
      .catch((err) => pushToast(err.message || 'Could not save the theme.'));
  };

  const chatHistory = settings?.chat_history !== false;
  const pickChatHistory = (on) => {
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
      .then(() => pushToast(on ? 'Chat history kept on this device.' : 'Chat history wiped from this device.', 'success'))
      .catch((err) => {
        onPatched({ chat_history: !on });
        pushToast(err.message || 'Could not save the setting.');
      });
  };

  return (
    <div className="page-inner">
      <PageHeader title="Settings" description="Hub and engine configuration." />

      <section className="panel" id="settings-appearance">
        <div className="panel-head"><h2>Appearance</h2></div>
        <div className="settings-row">
          <div>
            <h3>Theme</h3>
            <p>Light follows the Apps and Automations prototypes; dark follows the Home dashboard.</p>
          </div>
          <div className="seg" role="tablist">
            {['light', 'dark'].map((t) => (
              <button
                key={t}
                role="tab"
                aria-selected={theme === t}
                className={`seg-opt${theme === t ? ' seg-opt-active' : ''}`}
                onClick={() => pickTheme(t)}
              >
                {t === 'light' ? 'Light' : 'Dark'}
              </button>
            ))}
          </div>
        </div>
        <div className="settings-row">
          <div>
            <h3>Remember chat on this device</h3>
            <p>Keeps your last assistant conversation in this browser. Turning it off wipes it immediately.</p>
          </div>
          <div className="seg" role="tablist">
            {[true, false].map((v) => (
              <button
                key={String(v)}
                role="tab"
                aria-selected={chatHistory === v}
                className={`seg-opt${chatHistory === v ? ' seg-opt-active' : ''}`}
                onClick={() => pickChatHistory(v)}
              >
                {v ? 'On' : 'Off'}
              </button>
            ))}
          </div>
        </div>
      </section>

      <AiSection settings={settings} onPatched={onPatched} />

      <NotificationsSection settings={settings} onPatched={onPatched} />

      <BackupsSection />

      <section className="panel">
        <div className="panel-head"><h2>General</h2></div>
        <dl className="fact-grid">
          <div className="fact">
            <dt>Platform</dt>
            <dd>{platform ? platformLabel(platform.current) : '—'}</dd>
          </div>
          <div className="fact">
            <dt>Hub version</dt>
            <dd className="mono">{health?.version || '—'}</dd>
          </div>
          <div className="fact fact-wide">
            <dt>Supported platforms</dt>
            <dd>{platform ? platform.supported.map(platformLabel).join(' · ') : '—'}</dd>
          </div>
        </dl>
        <AddToHomeScreen appName="Vela" forHub />
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>App Environments</h2>
          <Link className="btn btn-small" to="/environments">Open Environments</Link>
        </div>
        <p className="panel-note">
          Apps run in the local engine on this machine — {engine ? `${engine.apps_installed ?? 0} installed, ${engine.apps_running ?? 0} running.` : 'status unavailable.'}
        </p>
      </section>

      <section className="panel">
        <div className="panel-head"><h2>Storage</h2></div>
        <dl className="fact-grid">
          <div className="fact">
            <dt>Used by Vela</dt>
            <dd>{engine ? formatBytes(engine.storage_bytes) : '—'}</dd>
          </div>
          <div className="fact fact-wide">
            <dt>Data directory</dt>
            <dd className="mono">{engine?.data_dir || '—'}</dd>
          </div>
        </dl>
      </section>

      <section className="panel">
        <div className="panel-head"><h2>Network</h2></div>
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
            <dt>App serving</dt>
            <dd className="mono">/apps/&lt;id&gt;/ (proxied inside the hub — apps never expose ports to the UI)</dd>
          </div>
        </dl>
      </section>

      <section className="panel panel-muted">
        <div className="settings-row settings-row-disabled">
          <div>
            <h3>Extensions</h3>
            <p>Extend the hub with community plugins.</p>
          </div>
          <span className="tag">Soon</span>
        </div>
      </section>
    </div>
  );
}
