import { useEffect, useRef, useState } from 'react';
import { ArrowUp, At, Square, SquaresFour } from '@phosphor-icons/react';
import { useApps } from '../store.jsx';

export default function ChatComposer({ input, setInput, busy, disabled, onSend, onStop, model }) {
  const { apps, error } = useApps();
  const field = useRef(null);
  const [mention, setMention] = useState(null);
  const [selected, setSelected] = useState(0);
  const matches = (apps ?? [])
    .filter(
      (app) =>
        app.installed &&
        `${app.name} ${app.id}`.toLowerCase().includes(mention?.query.toLowerCase() ?? ''),
    )
    .slice(0, 8);
  const activeIndex = Math.min(selected, Math.max(0, matches.length - 1));

  useEffect(() => {
    if (mention)
      document.getElementById(`app-mention-${activeIndex}`)?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex, mention]);

  useEffect(() => {
    const el = field.current;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
    if (!input) setMention(null);
  }, [input]);

  function detect(value, cursor) {
    const match = value.slice(0, cursor).match(/(?:^|\s)@([^\s@]*)$/);
    setMention(
      match ? { query: match[1], start: cursor - match[1].length - 1, end: cursor } : null,
    );
    setSelected(0);
  }

  function insert(app) {
    const token = `@${app.id} `;
    const next = input.slice(0, mention.start) + token + input.slice(mention.end);
    if (next.length > 4000) return;
    const cursor = mention.start + token.length;
    setInput(next);
    setMention(null);
    requestAnimationFrame(() => {
      field.current?.focus();
      field.current?.setSelectionRange(cursor, cursor);
    });
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
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(cursor, cursor);
    });
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
      if (!busy && !disabled) onSend(input);
    }
  }

  return (
    <div className="chat-dock">
      <form
        className="chat-composer"
        onSubmit={(event) => {
          event.preventDefault();
          if (!busy && !disabled) onSend(input);
        }}
      >
        {mention && (
          <div className="mention-picker">
            <div className="mention-heading">
              Mention an app <span>↑ ↓ to browse · Enter to select</span>
            </div>
            <div id="app-mentions" role="listbox" aria-label="Installed apps">
              {matches.map((app, index) => (
                <button
                  type="button"
                  role="option"
                  id={`app-mention-${index}`}
                  key={app.id}
                  aria-selected={index === activeIndex}
                  tabIndex={-1}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => insert(app)}
                >
                  <SquaresFour size={20} aria-hidden />
                  <span>
                    <strong>{app.name || app.id}</strong>
                    <small>@{app.id}</small>
                  </span>
                  <small>
                    {app.kind === 'connected-web' ? 'Web app' : app.running ? 'Running' : 'Stopped'}
                  </small>
                </button>
              ))}
            </div>
            {!matches.length && (
              <p role="status">
                {error
                  ? 'Apps unavailable. Try again when the server reconnects.'
                  : apps === null
                    ? 'Loading apps…'
                    : 'No installed apps match. Try a name or app ID.'}
              </p>
            )}
            <div className="mention-foot">
              Adds the app ID to your question. App data stays private.
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
          placeholder="Ask about your apps… Type @ to mention one"
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
            aria-label="Mention an app"
          >
            <At size={17} aria-hidden /> <span>App</span>
          </button>
          <span className="composer-model" title={model}>
            {model || 'Vela assistant'}
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
        <span>Enter to send · Shift+Enter for a new line</span>
        <span>
          {input.length > 3500
            ? `${input.length}/4000`
            : 'Answers can be mistaken. Check important details.'}
        </span>
      </div>
    </div>
  );
}
