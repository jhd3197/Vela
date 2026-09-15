import { useState } from 'react';
import Button from '../ui/Button.jsx';
import Dialog from '../ui/Dialog.jsx';
import FormField from '../ui/FormField.jsx';
import BotIcon from './BotIcon.jsx';

const MIN = 2;
const MAX = 4;

// Create a room, or repair one whose membership fell below two available bots.
export default function RoomDialog({ bots, room, onClose, onSave }) {
  const repairing = Boolean(room);
  const [title, setTitle] = useState(room?.title ?? '');
  const [purpose, setPurpose] = useState(room?.purpose ?? '');
  const [mode, setMode] = useState(room?.mode ?? 'mention');
  const [selected, setSelected] = useState(() =>
    (room?.botIds ?? []).filter((id) => bots.some((bot) => bot.id === id)),
  );
  const [lead, setLead] = useState(room?.leadBotId ?? '');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);

  const leadId = selected.includes(lead) ? lead : selected[0] || '';

  function toggle(botId) {
    setSelected((current) =>
      current.includes(botId)
        ? current.filter((id) => id !== botId)
        : current.length >= MAX
          ? current
          : [...current, botId],
    );
  }

  async function submit(event) {
    event.preventDefault();
    if (selected.length < MIN) {
      setError(`Pick at least ${MIN} bots.`);
      return;
    }
    setPending(true);
    setError('');
    try {
      await onSave({
        title: title.trim() || 'New room',
        purpose: purpose.trim(),
        mode,
        botIds: selected,
        leadBotId: leadId,
      });
      onClose();
    } catch (failure) {
      setError(failure?.message || 'Could not save this room.');
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog open onClose={onClose} pending={pending} aria-labelledby="room-dialog-title">
      <h2 id="room-dialog-title">{repairing ? `Edit ${room.title}` : 'New room'}</h2>
      <form className="room-form" onSubmit={submit}>
        {!repairing && (
          <div className="field-grid">
            <FormField label="Name">
              <input
                value={title}
                maxLength={120}
                autoComplete="off"
                placeholder="Content team"
                onChange={(event) => setTitle(event.target.value)}
              />
            </FormField>
          </div>
        )}

        <FormField
          label="What this room is for"
          hint="Optional. Every bot in the room is told this."
        >
          <textarea
            rows={2}
            value={purpose}
            maxLength={1000}
            onChange={(event) => setPurpose(event.target.value)}
          />
        </FormField>

        <fieldset className="room-members">
          <legend>
            Bots{' '}
            <span className="panel-note">
              {selected.length} of {MAX} — pick {MIN} to {MAX}
            </span>
          </legend>
          {!bots.length && (
            <p className="panel-note">Create at least two bots before making a room.</p>
          )}
          {bots.map((bot) => {
            const checked = selected.includes(bot.id);
            return (
              <label key={bot.id} className={`room-member${checked ? ' is-selected' : ''}`}>
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={!checked && selected.length >= MAX}
                  onChange={() => toggle(bot.id)}
                />
                <BotIcon bot={bot} size={15} />
                <span>
                  <strong>{bot.name}</strong>
                  <small>{bot.description || 'No description'}</small>
                </span>
              </label>
            );
          })}
        </fieldset>

        <fieldset className="room-mode">
          <legend>How they take turns</legend>
          <label className={mode === 'mention' ? 'is-selected' : ''}>
            <input
              type="radio"
              name="room-mode"
              checked={mode === 'mention'}
              onChange={() => setMode('mention')}
            />
            <span>
              <strong>Mention or lead</strong>
              <small>
                Only the bots you @mention answer. With no mention, the lead answers alone.
              </small>
            </span>
          </label>
          <label className={mode === 'roundtable' ? 'is-selected' : ''}>
            <input
              type="radio"
              name="room-mode"
              checked={mode === 'roundtable'}
              onChange={() => setMode('roundtable')}
            />
            <span>
              <strong>Roundtable</strong>
              <small>Every bot answers once, in order, each seeing the replies before it.</small>
            </span>
          </label>
        </fieldset>

        {selected.length > 0 && (
          <FormField label="Lead" hint="Answers when you send a message with no mention.">
            <select value={leadId} onChange={(event) => setLead(event.target.value)}>
              {selected.map((id) => (
                <option key={id} value={id}>
                  {bots.find((bot) => bot.id === id)?.name ?? id}
                </option>
              ))}
            </select>
          </FormField>
        )}

        <p className="panel-note">
          Everything a bot says in this room is visible to every other bot in it.
        </p>

        {error && (
          <p className="panel-note bot-draft-error" role="alert">
            {error}
          </p>
        )}

        <div className="dialog-actions">
          <Button onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            type="submit"
            pending={pending}
            disabled={selected.length < MIN}
          >
            {repairing ? 'Save room' : 'Create room'}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
