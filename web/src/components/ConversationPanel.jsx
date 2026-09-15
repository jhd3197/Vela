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
  UsersThree,
} from '@phosphor-icons/react';
import Button from './ui/Button.jsx';
import Dialog from './ui/Dialog.jsx';
import BotIcon from './bots/BotIcon.jsx';
import BotsList from './bots/BotsList.jsx';

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

// A room row shows who is in it, so the list distinguishes a three-bot room
// from a chat at a glance.
function RoomBadges({ conversation, botsById }) {
  const members = (conversation.botIds ?? []).map((id) => botsById.get(id)).filter(Boolean);
  if (!members.length) return null;
  return (
    <span className="room-badges" aria-hidden="true">
      {members.map((bot) => (
        <BotIcon key={bot.id} bot={bot} size={11} />
      ))}
    </span>
  );
}

function ConversationRow({
  conversation,
  active,
  botsById,
  onSelect,
  onRename,
  onArchive,
  onDelete,
}) {
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
        <span className="conversation-title">
          {conversation.kind === 'room' && (
            <UsersThree size={13} className="conversation-kind" aria-label="Room" />
          )}
          {conversation.kind !== 'room' && botsById.get(conversation.botId) && (
            <BotIcon bot={botsById.get(conversation.botId)} size={12} />
          )}
          {conversation.title}
        </span>
        <span className="conversation-meta">
          {conversation.kind === 'room' && (
            <RoomBadges conversation={conversation} botsById={botsById} />
          )}
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
  tab = 'chats',
  onTab,
  builtin,
  bots = [],
  botsLoading = false,
  botsError = '',
  onNewBot,
  onEditBot,
  onDuplicateBot,
  onArchiveBot,
  onDeleteBot,
  onChatWithBot,
  onNewRoom,
}) {
  const [renaming, setRenaming] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const botsById = new Map([
    ...(builtin ? [[builtin.id, builtin]] : []),
    ...bots.map((bot) => [bot.id, bot]),
  ]);
  const rooms = conversations.filter((conversation) => conversation.kind === 'room');
  const chats = conversations.filter((conversation) => conversation.kind !== 'room');
  const shown = tab === 'rooms' ? rooms : chats;

  const renderList = (items) => (
    <>
      {historyEnabled && !loading && !error && items.length === 0 && (
        <p className="panel-note">
          {query
            ? `No ${tab === 'rooms' ? 'room' : 'conversation'} matches “${query}”.`
            : showArchived
              ? 'Nothing is archived.'
              : tab === 'rooms'
                ? 'Rooms you create will be listed here.'
                : 'Your conversations will be listed here.'}
        </p>
      )}
      {group(items).map((section) => (
        <div className="conversation-group" key={section.label}>
          <p className="section-head">{section.label}</p>
          <ul>
            {section.items.map((conversation) => (
              <ConversationRow
                key={conversation.id}
                conversation={conversation}
                active={conversation.id === activeId}
                botsById={botsById}
                onSelect={onSelect}
                onRename={setRenaming}
                onArchive={onArchive}
                onDelete={setDeleting}
              />
            ))}
          </ul>
        </div>
      ))}
    </>
  );

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

      <div className="conversation-tabs" role="tablist" aria-label="Ask sections">
        {[
          ['chats', 'Chats'],
          ['bots', 'Bots'],
          ['rooms', 'Rooms'],
        ].map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            id={`ask-tab-${key}`}
            aria-selected={tab === key}
            aria-controls={`ask-panel-${key}`}
            className={tab === key ? 'is-active' : ''}
            onClick={() => onTab?.(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab !== 'bots' && (
        <div className="conversation-panel-new">
          <Button
            variant="primary"
            block
            onClick={tab === 'rooms' ? onNewRoom : onNew}
            disabled={tab === 'rooms' && bots.length < 2}
          >
            <Plus size={15} aria-hidden="true" />{' '}
            {tab === 'rooms' ? 'New room' : 'New conversation'}
          </Button>
          {tab === 'rooms' && bots.length < 2 && (
            <p className="panel-note">A room needs at least two of your own bots.</p>
          )}
        </div>
      )}

      {historyEnabled && tab !== 'bots' && (
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

      <div
        className="conversation-list"
        role="tabpanel"
        id={`ask-panel-${tab}`}
        aria-labelledby={`ask-tab-${tab}`}
      >
        {tab === 'bots' ? (
          <BotsList
            builtin={builtin}
            bots={bots}
            loading={botsLoading}
            error={botsError}
            onChat={onChatWithBot}
            onNew={onNewBot}
            onEdit={onEditBot}
            onDuplicate={onDuplicateBot}
            onArchive={onArchiveBot}
            onDelete={onDeleteBot}
          />
        ) : (
          <>
            {!historyEnabled && (
              <p className="panel-note">
                Chat history is turned off, so conversations are not saved. Your bots and rooms are
                kept as settings. Turn history on in Settings under Chat &amp; privacy to keep
                transcripts.
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
            {renderList(shown)}
          </>
        )}
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
