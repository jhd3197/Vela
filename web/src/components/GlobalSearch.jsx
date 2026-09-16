import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { GearSix, Heartbeat, MagnifyingGlass, Note, Receipt, Sparkle } from '@phosphor-icons/react';
import { useApps } from '../store.jsx';
import { coreApps } from '../navigation.js';
import { useDeveloperTools } from '../developer.js';
import AppIcon from './AppIcon.jsx';
import { useSettingsPopup } from './SettingsProvider.jsx';

// Settings destinations the palette can reach. Developer entries stay out of
// the results while the preference is off; searching for them then points at
// the preference itself rather than pretending the destination is missing.
const SETTINGS_ENTRIES = [
  { label: 'General', to: '/settings#general', keywords: 'about version platform phone' },
  { label: 'Appearance & theme', to: '/settings#appearance', keywords: 'light dark' },
  { label: 'Local AI', to: '/settings#ai', keywords: 'ollama model' },
  { label: 'Notifications', to: '/settings#notifications', keywords: 'ntfy push alerts' },
  {
    label: 'Updates',
    to: '/settings#updates',
    keywords: 'update upgrade version release notes download',
  },
  {
    label: 'Health',
    to: '/settings#health',
    keywords: 'doctor checks repair diagnose disk certificate runtime',
  },
  { label: 'Backups & storage', to: '/settings#backups', keywords: 'restore snapshot disk space' },
  { label: 'Chat & privacy', to: '/settings#chat', keywords: 'history retention' },
  {
    label: 'Developer tools',
    to: '/settings#developer',
    keywords: 'logs system network endpoint diagnostics',
    developer: true,
  },
  { label: 'System', to: '/environments', keywords: 'engine running storage', developer: true },
  {
    label: 'Show developer tools',
    to: '/settings#general',
    keywords: 'developer logs system diagnostics environments',
    whileOff: true,
  },
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
//
// `variant="hero"` is the centred, larger field the Desk and Launchpad put at
// the top of the screen. A caller can also drive the query itself (`value` +
// `onQueryChange`) and take Enter (`onEnter`) so the Launchpad filters its grid
// live while the same field still opens the ⌘K palette; passing
// `showResults={false}` then hands the result surface to that page.
export default function GlobalSearch({
  compact = false,
  variant = 'default',
  value,
  onQueryChange,
  onEnter,
  showResults = true,
  autoFocus = false,
  // The Launchpad filters a grid it is already showing rather than searching
  // everything, so it says how many apps are in front of you instead.
  placeholder = 'Search apps, notes, settings, or ask…',
}) {
  const { apps } = useApps();
  const navigate = useNavigate();
  const location = useLocation();
  const { openSettings } = useSettingsPopup();
  const developer = useDeveloperTools();
  const controlled = value !== undefined;
  const [internalQuery, setInternalQuery] = useState(
    () => sessionStorage.getItem('vela.launcher.query') || '',
  );
  const query = controlled ? value : internalQuery;
  const setQuery = (next) => {
    if (controlled) onQueryChange?.(next);
    else setInternalQuery(next);
  };
  useEffect(() => {
    if (!controlled) sessionStorage.setItem('vela.launcher.query', internalQuery);
  }, [controlled, internalQuery]);
  const [open, setOpen] = useState(false);
  // A compact header (Ask, app workspaces) keeps the same palette behind an
  // icon, so the shortcut and its results never disappear from a screen.
  const [expanded, setExpanded] = useState(false);
  const inputRef = useRef(null);
  const boxRef = useRef(null);
  const triggerRef = useRef(null);

  useEffect(() => {
    if (autoFocus) requestAnimationFrame(() => inputRef.current?.focus());
  }, [autoFocus]);

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
    if (!q) return { apps: [], core: [], settings: [], data: [] };
    const appHits = (apps || [])
      .filter(
        (a) => a.name.toLowerCase().includes(q) || (a.category || '').toLowerCase().includes(q),
      )
      .slice(0, 5);
    // Vela's own tools rank alongside apps: searching "auto" reaches
    // Automations, "market" reaches the Library.
    const coreHits = coreApps(developer)
      .filter((entry) => entry.label.toLowerCase().includes(q))
      .slice(0, 4);
    const settingHits = SETTINGS_ENTRIES.filter(
      (s) =>
        (s.developer ? developer : s.whileOff ? !developer : true) &&
        `${s.label} ${s.keywords || ''}`.toLowerCase().includes(q),
    ).slice(0, 3);
    return { apps: appHits, core: coreHits, settings: settingHits, data: searchAppData(q) };
  }, [query, apps, developer]);

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
    results.apps.length > 0 ||
    results.core.length > 0 ||
    results.settings.length > 0 ||
    results.data.length > 0;

  const box = (
    <div className={`searchbox${variant === 'hero' ? ' searchbox-hero' : ''}`} ref={boxRef}>
      <MagnifyingGlass className="searchbox-icon" size={variant === 'hero' ? 18 : 15} />
      <input
        ref={inputRef}
        type="search"
        placeholder={placeholder}
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && onEnter) {
            e.preventDefault();
            setOpen(false);
            onEnter(query.trim());
          }
        }}
        aria-label="Search"
      />
      <kbd className="searchbox-kbd">⌘K</kbd>
      {showResults && open && query.trim() && (
        <div className="search-pop" role="listbox">
          {results.apps.map((app) => (
            <button
              key={app.id}
              className="search-hit"
              onClick={() => go(app.installed ? `/app/${app.id}` : '/library')}
            >
              <AppIcon app={app} size={26} />
              <span className="search-hit-name">{app.name}</span>
              <span className="search-hit-hint">{app.installed ? 'Open' : 'In Marketplace'}</span>
            </button>
          ))}
          {results.core.map((entry) => (
            <button key={entry.id} className="search-hit" onClick={() => go(entry.to)}>
              <AppIcon
                app={{ id: entry.id, name: entry.label, glyph: entry.icon, color: entry.color }}
                size={26}
              />
              <span className="search-hit-name">{entry.label}</span>
              <span className="search-hit-hint">Vela</span>
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
