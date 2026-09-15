import { useEffect, useRef, useState } from 'react';
import {
  Archive,
  ArrowCounterClockwise,
  DotsThree,
  MagnifyingGlass,
  PencilSimple,
  Plus,
  SidebarSimple,
  Trash,
} from '@phosphor-icons/react';
import Button from './ui/Button.jsx';
import Dialog from './ui/Dialog.jsx';

// Date groups are derived from each conversation's own timestamp; nothing here
// is a fabricated bucket.
function groupLabel(iso) {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return 'Earlier';
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const days = Math.floor((start - when) / 86400000);
  if (days <= 0) return 'Today';
  if (days <= 1) return 'Yesterday';
  if (days < 7) return 'Earlier this week';
  if (days < 30) return 'Earlier this month';
  return 'Older';
}

function group(conversations) {
  const groups = [];
  for (const conversation of conversations) {
    const label = groupLabel(conversation.updatedAt);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.items.push(conversation);
    else groups.push({ label, items: [conversation] });
  }
  return groups;
}

function RenameDialog({ conversation, onClose, onSave }) {
  const [title, setTitle] = useState(conversation.title);
  const [pending, setPending] = useState(false);
  const input = useRef(null);
  return (
    <Dialog open onClose={onClose} pending={pending} aria-labelledby="rename-title">
      <h2 id="rename-title">Rename conversation</h2>
      <form
        className="source-field"
        onSubmit={async (event) => {
          event.preventDefault();
          if (!title.trim()) return;
          setPending(true);
          try {
            await onSave(title.trim());
            onClose();
          } finally {
            setPending(false);
          }
        }}
      >
        <label htmlFor="rename-input">Title</label>
        <input
          id="rename-input"
          ref={input}
          className="connection-input"
          value={title}
          maxLength={120}
          onChange={(event) => setTitle(event.target.value)}
          required
        />
        <div className="dialog-actions">
          <Button onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" pending={pending}>
            Save
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function DeleteDialog({ conversation, onClose, onConfirm }) {
  const [pending, setPending] = useState(false);
  const cancel = useRef(null);
  return (
    <Dialog
      open
      onClose={onClose}
      pending={pending}
      initialFocusRef={cancel}
      aria-labelledby="delete-title"
    >
      <h2 id="delete-title">Delete this conversation?</h2>
      <p className="dialog-note">
        “{conversation.title}” and its messages are removed from this server permanently. Archiving
        keeps a conversation and hides it instead.
      </p>
      <div className="dialog-actions">
        <Button ref={cancel} onClick={onClose} disabled={pending}>
          Cancel
        </Button>
        <Button
          variant="danger"
          pending={pending}
          onClick={async () => {
            setPending(true);
            try {
              await onConfirm();
              onClose();
            } finally {
              setPending(false);
            }
          }}
        >
          Delete permanently
        </Button>
      </div>
    </Dialog>
  );
}

function ConversationRow({ conversation, active, onSelect, onRename, onArchive, onDelete }) {
  const [menu, setMenu] = useState(false);
  const menuRef = useRef(null);
  const trigger = useRef(null);

  useEffect(() => {
    if (!menu) return undefined;
    const dismiss = (event) => {
      if (event.type === 'keydown' && event.key === 'Escape') {
        setMenu(false);
        trigger.current?.focus();
      }
      if (event.type === 'pointerdown' && !menuRef.current?.parentElement?.contains(event.target))
        setMenu(false);
    };
    addEventListener('keydown', dismiss);
    addEventListener('pointerdown', dismiss);
    return () => {
      removeEventListener('keydown', dismiss);
      removeEventListener('pointerdown', dismiss);
    };
  }, [menu]);

  return (
    <li className={`conversation-row${active ? ' conversation-row-active' : ''}`}>
      <button
        type="button"
        className="conversation-open"
        aria-current={active ? 'true' : undefined}
        onClick={() => onSelect(conversation)}
      >
        <span className="conversation-title">{conversation.title}</span>
        <span className="conversation-meta">
          {conversation.preview ||
            `${conversation.messageCount} ${conversation.messageCount === 1 ? 'message' : 'messages'}`}
        </span>
      </button>
      <div className="conversation-actions">
        <button
          ref={trigger}
          type="button"
          className="icon-btn conversation-more"
          aria-label={`Actions for ${conversation.title}`}
          aria-expanded={menu}
          onClick={() => setMenu((open) => !open)}
        >
          <DotsThree size={16} aria-hidden="true" />
        </button>
        {menu && (
          <div className="conversation-menu" ref={menuRef} role="menu">
            <button
              role="menuitem"
              onClick={() => {
                setMenu(false);
                onRename(conversation);
              }}
            >
              <PencilSimple size={14} aria-hidden="true" /> Rename
            </button>
            <button
              role="menuitem"
              onClick={() => {
                setMenu(false);
                onArchive(conversation);
              }}
            >
              {conversation.archived ? (
                <>
                  <ArrowCounterClockwise size={14} aria-hidden="true" /> Restore
                </>
              ) : (
                <>
                  <Archive size={14} aria-hidden="true" /> Archive
                </>
              )}
            </button>
            <button
              role="menuitem"
              onClick={() => {
                setMenu(false);
                onDelete(conversation);
              }}
            >
              <Trash size={14} aria-hidden="true" /> Delete
            </button>
          </div>
        )}
      </div>
    </li>
  );
}

// The Ask context panel: new conversation, search, date-grouped history and the
// per-conversation actions. It only ever shows what the server actually stored.
// On desktop it is a column beside the conversation; on narrower screens Ask
// places it in the navigation drawer, beside the rail (`drawer`).
export default function ConversationPanel({
  drawer = false,
  conversations,
  activeId,
  loading,
  error,
  historyEnabled,
  query,
  onQuery,
  showArchived,
  onShowArchived,
  onSelect,
  onNew,
  onRename,
  onArchive,
  onDelete,
  onCollapse,
  model,
  reachable,
}) {
  const [renaming, setRenaming] = useState(null);
  const [deleting, setDeleting] = useState(null);

  return (
    <aside
      className={`conversation-panel${drawer ? ' conversation-panel-drawer' : ' workspace-panel'}`}
      aria-label="Conversations"
    >
      <div className="conversation-panel-head">
        <span className="conversation-panel-title">Ask</span>
        <span className="conversation-panel-model">
          <span className={`dot${reachable ? ' dot-ok' : ' dot-bad'}`} aria-hidden="true" />
          {model || 'Local model'}
        </span>
        {!drawer && (
          <button
            type="button"
            className="btn btn-icon"
            aria-label="Collapse conversations"
            onClick={onCollapse}
          >
            <SidebarSimple size={16} aria-hidden="true" />
          </button>
        )}
      </div>

      <div className="conversation-panel-new">
        <Button variant="primary" block onClick={onNew}>
          <Plus size={15} aria-hidden="true" /> New conversation
        </Button>
      </div>

      {historyEnabled && (
        <div className="conversation-panel-search">
          <div className="searchbox">
            <MagnifyingGlass className="searchbox-icon" size={14} aria-hidden="true" />
            <input
              type="search"
              value={query}
              placeholder="Search"
              aria-label="Search conversations"
              onChange={(event) => onQuery(event.target.value)}
            />
          </div>
          <button
            type="button"
            className={`chip${showArchived ? ' chip-active' : ''}`}
            aria-pressed={showArchived}
            onClick={() => onShowArchived(!showArchived)}
          >
            Archived
          </button>
        </div>
      )}

      <div className="conversation-list">
        {!historyEnabled && (
          <p className="panel-note">
            Chat history is turned off, so conversations are not saved. Turn it on in Settings under
            Chat &amp; privacy to keep them.
          </p>
        )}
        {historyEnabled && loading && (
          <p className="panel-note" role="status">
            Loading conversations…
          </p>
        )}
        {historyEnabled && error && (
          <p className="panel-note" role="alert">
            {error}
          </p>
        )}
        {historyEnabled && !loading && !error && conversations.length === 0 && (
          <p className="panel-note">
            {query
              ? `No conversation matches “${query}”.`
              : showArchived
                ? 'Nothing is archived.'
                : 'Your conversations will be listed here.'}
          </p>
        )}
        {group(conversations).map((section) => (
          <div className="conversation-group" key={section.label}>
            <p className="section-head">{section.label}</p>
            <ul>
              {section.items.map((conversation) => (
                <ConversationRow
                  key={conversation.id}
                  conversation={conversation}
                  active={conversation.id === activeId}
                  onSelect={onSelect}
                  onRename={setRenaming}
                  onArchive={onArchive}
                  onDelete={setDeleting}
                />
              ))}
            </ul>
          </div>
        ))}
      </div>

      <p className="conversation-panel-foot">Runs on this machine</p>

      {renaming && (
        <RenameDialog
          conversation={renaming}
          onClose={() => setRenaming(null)}
          onSave={(title) => onRename(renaming, title)}
        />
      )}
      {deleting && (
        <DeleteDialog
          conversation={deleting}
          onClose={() => setDeleting(null)}
          onConfirm={() => onDelete(deleting)}
        />
      )}
    </aside>
  );
}
