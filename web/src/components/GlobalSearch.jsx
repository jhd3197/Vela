import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { GearSix, Heartbeat, MagnifyingGlass, Note, Receipt, Sparkle } from '@phosphor-icons/react';
import { useApps } from '../store.jsx';
import AppIcon from './AppIcon.jsx';
import { useSettingsPopup } from './SettingsProvider.jsx';

const SETTINGS_ENTRIES = [
  { label: 'General', to: '/settings' },
  { label: 'Appearance & theme', to: '/settings#appearance' },
  { label: 'Local AI', to: '/settings#ai' },
  { label: 'Notifications', to: '/settings#notifications' },
  { label: 'Backups', to: '/settings#backups' },
  { label: 'App Environments', to: '/environments' },
  { label: 'Chat & privacy', to: '/settings#chat' },
  { label: 'Storage', to: '/settings#storage' },
  { label: 'Network', to: '/settings#network' },
];

// Mini-apps keep their data in same-origin localStorage under vela.* keys, so
// the palette can search their content client-side. Reads are defensive:
// missing, non-JSON, or wrong-shaped values are skipped silently.
function readList(key) {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

const DATA_SOURCES = [
  {
    key: 'vela.notes.v1',
    appId: 'notes',
    appName: 'Notes',
    icon: Note,
    match: (n, q) =>
      typeof n?.title === 'string' &&
      (n.title.toLowerCase().includes(q) ||
        String(n.body || '')
          .toLowerCase()
          .includes(q)),
    label: (n) => n.title || 'Untitled note',
  },
  {
    key: 'vela.finance.v1',
    appId: 'finance',
    appName: 'Money',
    icon: Receipt,
    match: (t, q) =>
      (typeof t?.note === 'string' && t.note.toLowerCase().includes(q)) ||
      (typeof t?.category === 'string' && t.category.toLowerCase().includes(q)),
    label: (t) => t.note || t.category || 'Transaction',
  },
  {
    key: 'vela.health.habits.v1',
    appId: 'health',
    appName: 'Health',
    icon: Heartbeat,
    match: (h, q) => typeof h?.name === 'string' && h.name.toLowerCase().includes(q),
    label: (h) => h.name,
  },
];

function searchAppData(q, limit = 5) {
  const hits = [];
  for (const source of DATA_SOURCES) {
    for (const item of readList(source.key)) {
      try {
        if (source.match(item, q)) {
          hits.push({
            appId: source.appId,
            appName: source.appName,
            icon: source.icon,
            label: source.label(item),
          });
          if (hits.length >= limit) return hits;
        }
      } catch {
        // A single malformed entry shouldn't break the search.
      }
    }
  }
  return hits;
}

// Global search: ⌘K palette over apps, settings sections, and mini-app data,
// all client-side. It lives in the workspace header, not in a separate bar.
export default function GlobalSearch({ compact = false }) {
  const { apps } = useApps();
  const navigate = useNavigate();
  const location = useLocation();
  const { openSettings } = useSettingsPopup();
  const [query, setQuery] = useState(() => sessionStorage.getItem('vela.launcher.query') || '');
  useEffect(() => {
    sessionStorage.setItem('vela.launcher.query', query);
  }, [query]);
  const [open, setOpen] = useState(false);
  // A compact header (Ask, app workspaces) keeps the same palette behind an
  // icon, so the shortcut and its results never disappear from a screen.
  const [expanded, setExpanded] = useState(false);
  const inputRef = useRef(null);
  const boxRef = useRef(null);
  const triggerRef = useRef(null);

  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setExpanded(true);
        requestAnimationFrame(() => inputRef.current?.focus());
      }
      if (e.key === 'Escape') {
        setOpen(false);
        if (expanded) {
          setExpanded(false);
          triggerRef.current?.focus();
        }
        inputRef.current?.blur();
      }
    };
    const onClickAway = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target) && e.target !== triggerRef.current) {
        setOpen(false);
        setExpanded(false);
      }
    };
    window.addEventListener('keydown', onKey);
    window.addEventListener('mousedown', onClickAway);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('mousedown', onClickAway);
    };
  }, [expanded]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return { apps: [], settings: [], data: [] };
    const appHits = (apps || [])
      .filter(
        (a) => a.name.toLowerCase().includes(q) || (a.category || '').toLowerCase().includes(q),
      )
      .slice(0, 5);
    const settingHits = SETTINGS_ENTRIES.filter((s) => s.label.toLowerCase().includes(q)).slice(
      0,
      3,
    );
    return { apps: appHits, settings: settingHits, data: searchAppData(q) };
  }, [query, apps]);

  const go = (to) => {
    setOpen(false);
    setExpanded(false);
    if (!to.startsWith('/app/')) setQuery('');
    if (to.startsWith('/settings')) {
      openSettings(to.split('#')[1] || 'general');
      return;
    }
    navigate(to, {
      state: {
        returnTo: location.pathname.startsWith('/app/')
          ? '/apps'
          : location.pathname + location.search,
      },
    });
  };

  const hasResults =
    results.apps.length > 0 || results.settings.length > 0 || results.data.length > 0;

  const box = (
    <div className="searchbox" ref={boxRef}>
      <MagnifyingGlass className="searchbox-icon" size={15} />
      <input
        ref={inputRef}
        type="search"
        placeholder="Search apps, notes, settings, or ask…"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        aria-label="Search"
      />
      <kbd className="searchbox-kbd">⌘K</kbd>
      {open && query.trim() && (
        <div className="search-pop" role="listbox">
          {results.apps.map((app) => (
            <button
              key={app.id}
              className="search-hit"
              onClick={() => go(app.installed ? `/app/${app.id}` : '/library')}
            >
              <AppIcon app={app} size={26} />
              <span className="search-hit-name">{app.name}</span>
              <span className="search-hit-hint">{app.installed ? 'Open' : 'In Library'}</span>
            </button>
          ))}
          {results.data.map((hit, i) => (
            <button
              key={`${hit.appId}-${i}`}
              className="search-hit"
              onClick={() => go(`/app/${hit.appId}`)}
            >
              <span className="search-hit-glyph">
                <hit.icon size={15} />
              </span>
              <span className="search-hit-name">{hit.label}</span>
              <span className="search-hit-hint">{hit.appName}</span>
            </button>
          ))}
          {results.settings.map((s) => (
            <button key={s.label} className="search-hit" onClick={() => go(s.to)}>
              <span className="search-hit-glyph">
                <GearSix size={15} />
              </span>
              <span className="search-hit-name">{s.label}</span>
              <span className="search-hit-hint">Settings</span>
            </button>
          ))}
          <button
            className="search-hit"
            onClick={() => go(`/ask?q=${encodeURIComponent(query.trim())}`)}
          >
            <span className="search-hit-glyph">
              <Sparkle size={15} />
            </span>
            <span className="search-hit-name">Ask: {query.trim()}</span>
            <span className="search-hit-hint">Assistant</span>
          </button>
          {!hasResults && (
            <p className="search-empty">No matches for “{query.trim()}” — try asking instead.</p>
          )}
        </div>
      )}
    </div>
  );

  if (!compact) return box;
  return (
    <div className="searchbox-compact">
      <button
        ref={triggerRef}
        type="button"
        className="btn btn-icon"
        aria-label="Search apps, notes and settings"
        aria-expanded={expanded}
        onClick={() => {
          setExpanded((value) => !value);
          requestAnimationFrame(() => inputRef.current?.focus());
        }}
      >
        <MagnifyingGlass size={16} aria-hidden="true" />
      </button>
      {expanded && <div className="searchbox-float">{box}</div>}
    </div>
  );
}
