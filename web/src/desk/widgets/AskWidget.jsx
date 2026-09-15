// The newest conversation, and a way into a new one.
//
// The insight is real: the title and the last line of the most recently
// updated conversation, from `/api/chat/conversations`. When chat history is
// off, or nothing has been asked yet, the widget says so instead of inventing
// something Vela noticed.
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowUp } from '@phosphor-icons/react';
import { listConversations } from '../../chatApi.js';
import { useResource } from '../../hooks/useResource.js';
import { DeskEmpty } from './primitives.jsx';

const load = (options) => listConversations(options);

export default function AskWidget() {
  const navigate = useNavigate();
  const [draft, setDraft] = useState('');
  const { data, error } = useResource(load);
  const latest = Array.isArray(data) ? data[0] : null;

  const submit = (event) => {
    event.preventDefault();
    const text = draft.trim();
    navigate(text ? `/ask?q=${encodeURIComponent(text)}` : '/ask');
  };

  return (
    <div className="desk-ask">
      <div className="desk-ask-latest">
        {error ? (
          <DeskEmpty>Ask isn’t reachable right now.</DeskEmpty>
        ) : latest ? (
          <button
            type="button"
            className="desk-ask-open"
            onClick={() => navigate(`/ask/${latest.id}`)}
          >
            <span className="desk-ask-title">{latest.title || 'Untitled conversation'}</span>
            {latest.preview ? <span className="desk-ask-line">{latest.preview}</span> : null}
          </button>
        ) : (
          <DeskEmpty>Ask anything…</DeskEmpty>
        )}
      </div>
      <form className="desk-ask-form" onSubmit={submit}>
        <input
          type="text"
          className="desk-ask-input"
          aria-label="Ask Vela"
          placeholder="Ask anything…"
          value={draft}
          maxLength={4000}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button type="submit" className="desk-ask-send" aria-label="Open Ask with this question">
          <ArrowUp size={14} weight="bold" aria-hidden="true" />
        </button>
      </form>
    </div>
  );
}
