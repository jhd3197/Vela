import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ArrowUp, At, Square, SquaresFour, UsersThree } from '@phosphor-icons/react';
import { useApps } from '../store.jsx';
import useMediaQuery from '../hooks/useMediaQuery.js';
import { PHONE } from '../breakpoints.js';
import BotIcon from './bots/BotIcon.jsx';

export default function ChatComposer({
  input,
  setInput,
  busy,
  disabled,
  onSend,
  onStop,
  model,
  bots = [],
  room = false,
  mode = 'mention',
  leadBotId = '',
  botLabel = null,
}) {
  const { apps, error } = useApps();
  // The phone composer is a single rounded line, so its hint has to be short.
  const phone = useMediaQuery(PHONE);
  const field = useRef(null);
  const [mention, setMention] = useState(null);
  const [selected, setSelected] = useState(0);
  // Bot mentions resolve to ids as they are inserted. The typed text is only a
  // label: routing always uses the id, so two bots with the same display name
  // can never be confused for one another.
  const [recipients, setRecipients] = useState([]);
  const query = mention?.query.toLowerCase() ?? '';

  const botMatches = room
    ? [
        ...('all'.startsWith(query) || !query ? [{ id: 'all', name: 'Everyone', all: true }] : []),
        ...bots.filter((bot) => bot.name.toLowerCase().includes(query)),
      ].slice(0, 6)
    : [];
  const appMatches = (apps ?? [])
    .filter((app) => app.installed && `${app.name} ${app.id}`.toLowerCase().includes(query))
    .slice(0, room ? 4 : 8);
  // One flat list for keyboard navigation; two labelled groups on screen.
  const matches = [
    ...botMatches.map((bot) => ({ kind: 'bot', entity: bot })),
    ...appMatches.map((app) => ({ kind: 'app', entity: app })),
  ];
  const activeIndex = Math.min(selected, Math.max(0, matches.length - 1));

  useEffect(() => {
    if (mention)
      document.getElementById(`app-mention-${activeIndex}`)?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex, mention]);

  // Where the caret belongs after the box's text was changed for the person
  // rather than by them — inserting a mention, or opening the picker.
  //
  // It has to be put back in the same frame the new text is painted in. On a
  // requestAnimationFrame it can land a keystroke late instead: someone who
  // picks a mention and immediately types a newline gets the caret yanked back
  // in front of it, and their next words go before the break rather than after.
  // A layout effect runs as soon as the DOM carries the new value and before
  // anything else can be typed.
  const caret = useRef(null);
  useLayoutEffect(() => {
    if (caret.current === null) return;
    const at = caret.current;
    caret.current = null;
    field.current?.focus();
    field.current?.setSelectionRange(at, at);
  });

  useEffect(() => {
    const el = field.current;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
    if (!input) setMention(null);
    // A recipient whose label was deleted from the box is no longer addressed.
    setRecipients((current) => current.filter((entry) => input.includes(`@${entry.label}`)));
  }, [input]);

  function detect(value, cursor) {
    const match = value.slice(0, cursor).match(/(?:^|\s)@([^\s@]*)$/);
    setMention(
      match ? { query: match[1], start: cursor - match[1].length - 1, end: cursor } : null,
    );
    setSelected(0);
  }

  function insert(match) {
    // A bot is addressed by name in the text but by id in the request.
    const label = match.kind === 'bot' ? match.entity.name.replace(/\s+/g, '') : match.entity.id;
    const token = `@${label} `;
    const next = input.slice(0, mention.start) + token + input.slice(mention.end);
    if (next.length > 4000) return;
    const cursor = mention.start + token.length;
    setInput(next);
    if (match.kind === 'bot')
      setRecipients((current) => [
        ...current.filter((entry) => entry.id !== match.entity.id),
        { id: match.entity.id, label },
      ]);
    setMention(null);
    caret.current = cursor;
  }

  function send() {
    if (busy || disabled) return;
    onSend(
      input,
      recipients.map((entry) => entry.id),
    );
    setRecipients([]);
  }

  function openPicker() {
    const el = field.current;
    const start = el.selectionStart;
    const prefix = start > 0 && !/\s/.test(input[start - 1]) ? ' @' : '@';
    const next = input.slice(0, start) + prefix + input.slice(el.selectionEnd);
    if (next.length > 4000) return;
    setInput(next);
    const cursor = start + prefix.length;
    detect(next, cursor);
    caret.current = cursor;
  }

  function keyDown(event) {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (mention) {
      if (event.key === 'Escape') {
        event.preventDefault();
        setMention(null);
        return;
      }
      if (matches.length && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
        event.preventDefault();
        setSelected(
          (activeIndex + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length,
        );
        return;
      }
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        if (matches.length) insert(matches[activeIndex]);
        return;
      }
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  }

  // Who this message will actually reach, said before it is sent.
  const addressed = recipients.length
    ? recipients.some((entry) => entry.id === 'all')
      ? 'Everyone in this room'
      : recipients.map((entry) => entry.label).join(', ')
    : mode === 'roundtable'
      ? 'Everyone, in order'
      : (bots.find((bot) => bot.id === leadBotId)?.name ?? bots[0]?.name ?? 'the lead');

  return (
    <div className="chat-dock">
      <form
        className="chat-composer"
        onSubmit={(event) => {
          event.preventDefault();
          send();
        }}
      >
        {mention && (
          <div className="mention-picker">
            <div className="mention-heading">
              {room ? 'Mention a bot or app' : 'Mention an app'}{' '}
              <span>↑ ↓ to browse · Enter to select</span>
            </div>
            <div
              id="app-mentions"
              role="listbox"
              aria-label={room ? 'Bots and apps' : 'Installed apps'}
            >
              {matches.map((match, index) => {
                const entity = match.entity;
                return (
                  <button
                    type="button"
                    role="option"
                    id={`app-mention-${index}`}
                    key={`${match.kind}-${entity.id}`}
                    aria-selected={index === activeIndex}
                    tabIndex={-1}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => insert(match)}
                  >
                    {match.kind === 'bot' ? (
                      entity.all ? (
                        <UsersThree size={20} aria-hidden />
                      ) : (
                        <BotIcon bot={entity} size={16} />
                      )
                    ) : (
                      <SquaresFour size={20} aria-hidden />
                    )}
                    <span>
                      <strong>{entity.name || entity.id}</strong>
                      <small>
                        {match.kind === 'bot'
                          ? entity.all
                            ? 'Every bot in this room'
                            : entity.description || 'Bot in this room'
                          : `@${entity.id}`}
                      </small>
                    </span>
                    {/* The group each entry belongs to, so a bot and an app of
                        the same name are never mistaken for each other. */}
                    <small>
                      {match.kind === 'bot'
                        ? 'Bot'
                        : entity.kind === 'connected-web'
                          ? 'Web app'
                          : entity.running
                            ? 'Running'
                            : 'Stopped'}
                    </small>
                  </button>
                );
              })}
            </div>
            {!matches.length && (
              <p role="status">
                {error
                  ? 'Apps unavailable. Try again when the server reconnects.'
                  : apps === null
                    ? 'Loading apps…'
                    : room
                      ? 'No bot or installed app matches.'
                      : 'No installed apps match. Try a name or app ID.'}
              </p>
            )}
            <div className="mention-foot">
              {room
                ? 'Bots answer; app mentions only add the app ID to your message.'
                : 'Adds the app ID to your question. App data stays private.'}
            </div>
          </div>
        )}
        <textarea
          ref={field}
          value={input}
          rows={1}
          maxLength={4000}
          role="combobox"
          aria-autocomplete="list"
          aria-haspopup="listbox"
          aria-expanded={Boolean(mention)}
          aria-controls={mention ? 'app-mentions' : undefined}
          aria-activedescendant={
            mention && matches.length ? `app-mention-${activeIndex}` : undefined
          }
          aria-label="Ask a question"
          aria-describedby="composer-hint"
          placeholder={phone ? 'Message Ask…' : 'Ask about your apps… Type @ to mention one'}
          onChange={(event) => {
            setInput(event.target.value);
            detect(event.target.value, event.target.selectionStart);
          }}
          onClick={(event) => detect(input, event.currentTarget.selectionStart)}
          onKeyUp={(event) => {
            if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key))
              detect(input, event.currentTarget.selectionStart);
          }}
          onBlur={() => setMention(null)}
          onKeyDown={keyDown}
        />
        <div className="composer-toolbar">
          <button
            type="button"
            className="composer-mention"
            onMouseDown={(event) => event.preventDefault()}
            onClick={openPicker}
            aria-label={room ? 'Mention a bot or app' : 'Mention an app'}
          >
            <At size={17} aria-hidden /> <span>{room ? 'Mention' : 'App'}</span>
          </button>
          <span className="composer-model" title={model}>
            {botLabel ?? model ?? 'Vela assistant'}
          </span>
          {busy ? (
            <button
              key="stop"
              type="button"
              className="composer-send composer-stop"
              onClick={(event) => {
                event.preventDefault();
                onStop();
              }}
              aria-label="Stop response"
            >
              <Square size={16} weight="fill" aria-hidden />
            </button>
          ) : (
            <button
              key="send"
              className="composer-send"
              type="submit"
              disabled={!input.trim() || disabled}
              aria-label="Send"
            >
              <ArrowUp size={20} aria-hidden />
            </button>
          )}
        </div>
      </form>
      <div className="composer-hint" id="composer-hint">
        <span>
          {room ? (
            <>
              Goes to <strong>{addressed}</strong> · Enter to send
            </>
          ) : (
            'Enter to send · Shift+Enter for a new line'
          )}
        </span>
        <span>
          {input.length > 3500
            ? `${input.length}/4000`
            : 'Answers can be mistaken. Check important details.'}
        </span>
      </div>
    </div>
  );
}
