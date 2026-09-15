import { useEffect, useRef, useState } from 'react';
import { MagicWand, Play, Square, X } from '@phosphor-icons/react';
import Button from '../ui/Button.jsx';
import Drawer from '../ui/Drawer.jsx';
import FormField from '../ui/FormField.jsx';
import BotIcon, { BOT_COLORS, BOT_ICONS } from './BotIcon.jsx';
import { draftInstructions } from '../../botsApi.js';
import { previewBot } from '../../chatApi.js';

// What each grantable tool actually reads, in the person's words. The server
// owns the list; this only explains it.
const TOOL_LABELS = {
  list_apps: ['See the app list', 'Names, and whether each app is installed and running.'],
  app_status: ['Check one app', 'Whether it is running, plus its port and uptime.'],
  app_logs: ['Read app logs', 'The most recent lines an app wrote.'],
  engine_status: ['Check the hub', 'Installed and running counts, and storage used.'],
};

// Instruction presets. They are starting text, not claims about what a bot can
// reach — every one of them starts with no tools.
const STARTERS = [
  {
    name: 'Writer',
    icon: 'pen-nib',
    color: 'violet',
    description: 'Drafts and edits copy',
    instructions:
      'You help draft and edit writing. Ask what the piece is for and who reads it if that is ' +
      'unclear. Prefer plain, concrete language over marketing phrasing. Offer one clear draft ' +
      'rather than several options unless asked. Keep the author’s voice.',
  },
  {
    name: 'Planner',
    icon: 'compass',
    color: 'teal',
    description: 'Breaks work into steps',
    instructions:
      'You turn a goal into a small number of concrete steps. Put them in the order they have ' +
      'to happen and say what each one depends on. Name the first step precisely enough to ' +
      'start today. Flag anything that looks like a blocker rather than planning around it.',
  },
  {
    name: 'Reviewer',
    icon: 'magnifying-glass',
    color: 'amber',
    description: 'Critiques work honestly',
    instructions:
      'You review work critically and specifically. Lead with the most serious problem. Quote ' +
      'the part you mean rather than describing it. Say what would make it better, not only ' +
      'what is wrong. If something is genuinely good, say so briefly and move on.',
  },
];

const BLANK = {
  name: '',
  description: '',
  icon: 'sparkle',
  color: 'indigo',
  instructions: '',
  model: '',
  tools: [],
};

export default function BotEditor({ bot, models = [], defaultModel, onClose, onSave }) {
  const editing = Boolean(bot?.id);
  const [form, setForm] = useState(() => ({ ...BLANK, ...(bot ?? {}) }));
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  const [purpose, setPurpose] = useState('');
  const [drafting, setDrafting] = useState(false);
  const [draftError, setDraftError] = useState('');

  const [preview, setPreview] = useState('');
  const [previewAsking, setPreviewAsking] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const [previewInput, setPreviewInput] = useState('');
  const previewController = useRef(null);

  useEffect(() => () => previewController.current?.abort(), []);

  const set = (patch) => setForm((current) => ({ ...current, ...patch }));

  function applyStarter(starter) {
    set({
      name: form.name || starter.name,
      description: form.description || starter.description,
      icon: starter.icon,
      color: starter.color,
      instructions: starter.instructions,
    });
  }

  async function draft() {
    if (!purpose.trim()) return;
    setDrafting(true);
    setDraftError('');
    const result = await draftInstructions(purpose.trim());
    setDrafting(false);
    if (result?.ok && result.instructions) set({ instructions: result.instructions });
    else
      setDraftError(
        result?.error || 'Could not draft instructions. Write them below and save — that works.',
      );
  }

  async function runPreview() {
    const message = previewInput.trim();
    if (!message || previewAsking) return;
    const ac = new AbortController();
    previewController.current = ac;
    setPreviewAsking(true);
    setPreview('');
    setPreviewError('');
    let text = '';
    try {
      await previewBot({
        name: form.name || 'Preview',
        instructions: form.instructions,
        model: form.model,
        message,
        signal: ac.signal,
        onEvent: (event) => {
          if (event.error) setPreviewError(event.error);
          if (typeof event.text === 'string') {
            text = text && event.text.startsWith(text) ? event.text : text + event.text;
            setPreview(text);
          }
        },
      });
    } catch (failure) {
      if (failure?.name !== 'AbortError')
        setPreviewError(failure?.message || 'The preview could not run.');
    } finally {
      previewController.current = null;
      setPreviewAsking(false);
    }
  }

  async function save(event) {
    event.preventDefault();
    if (!form.name.trim()) {
      setError('Give this bot a name.');
      return;
    }
    setSaving(true);
    setError('');
    try {
      await onSave({
        name: form.name.trim(),
        description: form.description.trim(),
        icon: form.icon,
        color: form.color,
        instructions: form.instructions,
        model: form.model,
        tools: form.tools,
      });
      onClose();
    } catch (failure) {
      setError(failure?.message || 'Could not save this bot.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <Drawer open onClose={onClose} panelClassName="bot-editor" aria-labelledby="bot-editor-title">
      <header className="bot-editor-head">
        <h2 id="bot-editor-title">{editing ? `Edit ${bot.name}` : 'New bot'}</h2>
        <button type="button" className="btn btn-icon" aria-label="Close" onClick={onClose}>
          <X size={16} aria-hidden="true" />
        </button>
      </header>

      <form className="bot-editor-body" onSubmit={save}>
        {!editing && (
          <section className="bot-starters" aria-label="Start from an example">
            <p className="panel-note">
              Start from an example, or write your own. Examples are instructions only — every new
              bot starts with no access to this hub.
            </p>
            <div className="bot-starter-row">
              {STARTERS.map((starter) => (
                <button
                  type="button"
                  key={starter.name}
                  className="bot-starter"
                  onClick={() => applyStarter(starter)}
                >
                  <BotIcon bot={starter} size={15} />
                  <span>
                    <strong>{starter.name}</strong>
                    <small>{starter.description}</small>
                  </span>
                </button>
              ))}
            </div>
          </section>
        )}

        <div className="field-grid">
          <FormField label="Name">
            <input
              value={form.name}
              maxLength={60}
              autoComplete="off"
              onChange={(event) => set({ name: event.target.value })}
            />
          </FormField>
          <FormField label="What it is for" hint="Shown in the bot list. Optional.">
            <input
              value={form.description}
              maxLength={200}
              autoComplete="off"
              onChange={(event) => set({ description: event.target.value })}
            />
          </FormField>
        </div>

        <fieldset className="bot-appearance">
          <legend>Icon and colour</legend>
          <div className="bot-icon-grid" role="radiogroup" aria-label="Icon">
            {Object.keys(BOT_ICONS).map((icon) => (
              <button
                type="button"
                key={icon}
                role="radio"
                aria-checked={form.icon === icon}
                aria-label={icon.replace(/-/g, ' ')}
                className={form.icon === icon ? 'is-selected' : ''}
                onClick={() => set({ icon })}
              >
                <BotIcon bot={{ icon, color: form.color }} size={16} />
              </button>
            ))}
          </div>
          <div className="bot-color-grid" role="radiogroup" aria-label="Colour">
            {BOT_COLORS.map((color) => (
              <button
                type="button"
                key={color}
                role="radio"
                aria-checked={form.color === color}
                aria-label={color}
                className={`bot-swatch bot-color-${color}${form.color === color ? ' is-selected' : ''}`}
                onClick={() => set({ color })}
              />
            ))}
          </div>
        </fieldset>

        <section className="bot-draft" aria-label="Draft instructions">
          <FormField
            label="Describe what this bot should do"
            hint="Optional. Vela can turn a description into instructions you can edit."
          >
            <input
              value={purpose}
              maxLength={600}
              placeholder="e.g. reviews my writing and is blunt about what is weak"
              onChange={(event) => setPurpose(event.target.value)}
            />
          </FormField>
          <Button size="small" onClick={draft} pending={drafting} disabled={!purpose.trim()}>
            <MagicWand size={14} aria-hidden="true" /> Draft instructions
          </Button>
          {draftError && (
            <p className="panel-note bot-draft-error" role="status">
              {draftError}
            </p>
          )}
        </section>

        <FormField
          label="Instructions"
          hint="How this bot should behave. Instructions never grant it access to anything."
        >
          <textarea
            rows={8}
            value={form.instructions}
            maxLength={8000}
            onChange={(event) => set({ instructions: event.target.value })}
          />
        </FormField>

        <FormField label="Model" hint="Leave on the server default unless this bot needs another.">
          <select value={form.model} onChange={(event) => set({ model: event.target.value })}>
            <option value="">Use server default{defaultModel ? ` (${defaultModel})` : ''}</option>
            {models.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </FormField>

        <fieldset className="bot-tools">
          <legend>What it can read</legend>
          <p className="panel-note">
            New bots can read nothing. Anything you allow here is read-only, and whatever this bot
            says in a room is visible to everyone in that room.
          </p>
          {Object.entries(TOOL_LABELS).map(([tool, [label, detail]]) => (
            <label key={tool} className="bot-tool">
              <input
                type="checkbox"
                checked={form.tools.includes(tool)}
                onChange={(event) =>
                  set({
                    tools: event.target.checked
                      ? [...form.tools, tool]
                      : form.tools.filter((t) => t !== tool),
                  })
                }
              />
              <span>
                <strong>{label}</strong>
                <small>{detail}</small>
              </span>
            </label>
          ))}
        </fieldset>

        <section className="bot-preview" aria-label="Try it">
          <h3>Try it</h3>
          <p className="panel-note">
            Answers with the instructions above and no tools. Nothing here is saved.
          </p>
          <div className="bot-preview-ask">
            <input
              value={previewInput}
              maxLength={2000}
              placeholder="Ask something…"
              onChange={(event) => setPreviewInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  runPreview();
                }
              }}
            />
            {previewAsking ? (
              <Button size="small" onClick={() => previewController.current?.abort()}>
                <Square size={13} weight="fill" aria-hidden="true" /> Stop
              </Button>
            ) : (
              <Button size="small" onClick={runPreview} disabled={!previewInput.trim()}>
                <Play size={13} aria-hidden="true" /> Try
              </Button>
            )}
          </div>
          {(preview || previewAsking) && (
            <div className="bot-preview-answer" role="status">
              {preview || 'Thinking…'}
            </div>
          )}
          {previewError && (
            <p className="panel-note bot-draft-error" role="alert">
              {previewError}
            </p>
          )}
        </section>

        {error && (
          <p className="panel-note bot-draft-error" role="alert">
            {error}
          </p>
        )}

        <div className="bot-editor-actions">
          <Button onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" pending={saving}>
            {editing ? 'Save changes' : 'Create bot'}
          </Button>
        </div>
      </form>
    </Drawer>
  );
}
