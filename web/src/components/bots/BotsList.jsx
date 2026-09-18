import { useState } from 'react';
import {
  Archive,
  ArrowCounterClockwise,
  ChatCircleText,
  Copy,
  DotsThree,
  PencilSimple,
  Plus,
  Trash,
} from '@phosphor-icons/react';
import Button from '../ui/Button.jsx';
import BotIcon from './BotIcon.jsx';
import { useConfirm } from '../../hooks/useConfirm.js';

function BotRow({ bot, onChat, onEdit, onDuplicate, onArchive, onDelete }) {
  const [menu, setMenu] = useState(false);
  return (
    <li className="bot-row">
      <button type="button" className="bot-open" onClick={() => onChat(bot)}>
        <BotIcon bot={bot} size={16} />
        <span className="bot-row-text">
          <span className="bot-row-name">{bot.name}</span>
          <span className="bot-row-meta">
            {bot.description ||
              (bot.tools.length
                ? `${bot.tools.length} ${bot.tools.length === 1 ? 'tool' : 'tools'}`
                : 'No tools')}
          </span>
        </span>
      </button>
      <div className="conversation-actions">
        <button
          type="button"
          className="icon-btn conversation-more"
          aria-label={`Actions for ${bot.name}`}
          aria-expanded={menu}
          onClick={() => setMenu((open) => !open)}
          onBlur={() => requestAnimationFrame(() => setMenu(false))}
        >
          <DotsThree size={16} aria-hidden="true" />
        </button>
        {menu && (
          <div className="conversation-menu" role="menu">
            <button role="menuitem" onMouseDown={() => onChat(bot)}>
              <ChatCircleText size={14} aria-hidden="true" /> New chat
            </button>
            <button role="menuitem" onMouseDown={() => onEdit(bot)}>
              <PencilSimple size={14} aria-hidden="true" /> Edit
            </button>
            <button role="menuitem" onMouseDown={() => onDuplicate(bot)}>
              <Copy size={14} aria-hidden="true" /> Duplicate
            </button>
            <button role="menuitem" onMouseDown={() => onArchive(bot)}>
              {bot.archived ? (
                <>
                  <ArrowCounterClockwise size={14} aria-hidden="true" /> Restore
                </>
              ) : (
                <>
                  <Archive size={14} aria-hidden="true" /> Archive
                </>
              )}
            </button>
            <button role="menuitem" onMouseDown={() => onDelete(bot)}>
              <Trash size={14} aria-hidden="true" /> Delete
            </button>
          </div>
        )}
      </div>
    </li>
  );
}

// The Bots tab of the Ask panel: the built-in assistant, then whatever the
// person has made.
export default function BotsList({
  builtin,
  bots,
  loading,
  error,
  onChat,
  onNew,
  onEdit,
  onDuplicate,
  onArchive,
  onDelete,
}) {
  const confirm = useConfirm();
  const askToDelete = (bot) =>
    confirm({
      title: `Delete ${bot.name}?`,
      message:
        `Chats this bot already answered keep their history, and still show that ${bot.name} ` +
        'wrote them. You will not be able to send it anything new.',
      confirmText: 'Delete bot',
      pendingText: 'Deleting…',
      onConfirm: () => onDelete(bot),
    });

  return (
    <div className="bots-list">
      <Button size="small" block onClick={onNew}>
        <Plus size={14} aria-hidden="true" /> New bot
      </Button>

      {error && (
        <p className="panel-note" role="alert">
          {error}
        </p>
      )}

      <p className="conversation-group-label">Built in</p>
      <ul className="bot-rows">
        <li className="bot-row">
          <button type="button" className="bot-open" onClick={() => onChat(builtin)}>
            <BotIcon bot={builtin} size={16} />
            <span className="bot-row-text">
              <span className="bot-row-name">{builtin?.name ?? 'Vela'}</span>
              <span className="bot-row-meta">Knows this hub. Reads apps, logs and storage.</span>
            </span>
          </button>
        </li>
      </ul>

      <p className="conversation-group-label">Your bots</p>
      {loading && <p className="panel-note">Loading bots…</p>}
      {!loading && !bots.length && (
        <p className="panel-note">
          No bots yet. A bot is a saved set of instructions and a model you can chat with, or put in
          a room with others.
        </p>
      )}
      <ul className="bot-rows">
        {bots.map((bot) => (
          <BotRow
            key={bot.id}
            bot={bot}
            onChat={onChat}
            onEdit={onEdit}
            onDuplicate={onDuplicate}
            onArchive={onArchive}
            onDelete={askToDelete}
          />
        ))}
      </ul>
    </div>
  );
}
