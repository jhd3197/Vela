import { useResource } from '../hooks/useResource.js';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSettingsPopup } from './SettingsProvider.jsx';
import { Bell, ChartBar, Flask, PaperPlaneTilt, WarningCircle } from '@phosphor-icons/react';
import { api, relTime } from '../api.js';
import { useOperationsContext } from '../operations/OperationsProvider.jsx';
import OperationsList from '../operations/OperationsList.jsx';

const POLL_INTERVAL = 30000;
const SEEN_KEY = 'vela-notifications-seen';
const MAX_ITEMS = 15;

const KIND_META = {
  publish: { icon: PaperPlaneTilt, label: 'Sent to phone' },
  test: { icon: Flask, label: 'Test' },
  digest: { icon: ChartBar, label: 'Digest' },
  status_alert: { icon: WarningCircle, label: 'Status alert' },
};

function getSeenAt() {
  try {
    return localStorage.getItem(SEEN_KEY) || '';
  } catch {
    return '';
  }
}

// Live notification bell: the hub's recent-events feed, badged for anything
// newer than the last time the panel was opened, and — above it — whatever is
// waiting on a person right now, from the one list the rail's dot, the desk
// and the System page also read. An event is something that happened; an
// operation is something that has not finished, and the second is what a badge
// is actually for.
export default function NotificationBell() {
  const { openSettings } = useSettingsPopup();
  const { needsAttention } = useOperationsContext();
  const { data } = useResource(api.getNotifications, { intervalMs: POLL_INTERVAL });
  const items = data?.notifications ?? null;
  const [open, setOpen] = useState(false);
  const [seenAt, setSeenAt] = useState(getSeenAt);
  const boxRef = useRef(null);

  useEffect(() => {
    const onClickAway = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('mousedown', onClickAway);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('mousedown', onClickAway);
      window.removeEventListener('keydown', onKey);
    };
  }, []);

  const unseen = useMemo(() => {
    if (!items) return 0;
    const seen = Date.parse(seenAt) || 0;
    return items.filter((n) => (Date.parse(n.timestamp) || 0) > seen).length;
  }, [items, seenAt]);
  // Opening the panel marks the events as seen; it does not answer anything,
  // so what needs a person keeps counting until it is dealt with.
  const unread = unseen + needsAttention.length;

  const toggle = () => {
    setOpen((prev) => {
      const next = !prev;
      if (next) {
        // Opening the panel marks everything currently listed as seen.
        const now = new Date().toISOString();
        try {
          localStorage.setItem(SEEN_KEY, now);
        } catch {
          // Private mode etc. — badge just resets for this session.
        }
        setSeenAt(now);
      }
      return next;
    });
  };

  return (
    <div className="notif-wrap" ref={boxRef}>
      <button
        className="icon-btn"
        aria-label={unread > 0 ? `Notifications (${unread} unread)` : 'Notifications'}
        title="Notifications"
        aria-expanded={open}
        onClick={toggle}
      >
        <Bell size={17} weight={unread > 0 ? 'fill' : 'regular'} />
        {unread > 0 && <span className="notif-badge">{unread > 9 ? '9+' : unread}</span>}
      </button>
      {open && (
        <div className="notif-pop" role="dialog" aria-label="Notifications">
          <div className="notif-head">
            <h2>Notifications</h2>
          </div>
          {needsAttention.length > 0 && (
            <div className="notif-attention">
              <h3 className="notif-section">Needs you</h3>
              <OperationsList operations={needsAttention} />
            </div>
          )}
          {items === null && <p className="notif-empty">Checking…</p>}
          {items !== null && items.length === 0 && (
            <p className="notif-empty">No notifications yet.</p>
          )}
          {items !== null && items.length > 0 && (
            <ul className="notif-list">
              {items.slice(0, MAX_ITEMS).map((n, i) => {
                const meta = KIND_META[n.kind] || { icon: Bell, label: n.kind || 'Event' };
                const Icon = meta.icon;
                return (
                  <li key={`${n.timestamp}-${i}`} className="notif-item">
                    <span className="notif-item-icon">
                      <Icon size={14} />
                    </span>
                    <span className="notif-item-text">
                      <span className="notif-item-title">{n.title}</span>
                      <span className="notif-item-sub">
                        {meta.label} · {relTime(n.timestamp)}
                      </span>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
          <button
            type="button"
            className="notif-foot"
            onClick={() => {
              setOpen(false);
              openSettings('notifications');
            }}
          >
            Notification settings
          </button>
        </div>
      )}
    </div>
  );
}
