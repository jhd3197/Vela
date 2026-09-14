import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  ArrowDown,
  ArrowClockwise,
  Broadcast,
  CheckCircle,
  Copy,
  CircleNotch,
  Eye,
  HardDrives,
  Play,
  Plus,
  Pulse,
  Scroll,
  Sparkle,
  SquaresFour,
  Warning,
  Wrench,
} from '@phosphor-icons/react';
import { getAiStatus, getSettings, sendToPhone, streamChat } from '../chatApi.js';
import AskContext from '../components/AskContext.jsx';
import ChatComposer from '../components/ChatComposer.jsx';
const Markdown = lazy(() => import('../components/ChatMarkdown.jsx'));

function ChatMarkdown({ children }) {
  return (
    <Suspense fallback={<div className="chat-markdown">{children}</div>}>
      <Markdown>{children}</Markdown>
    </Suspense>
  );
}

// Ask: streaming local chat (POST /api/chat, SSE), rendered as a transcript
// of what actually happened — the question, each tool the assistant reached
// for with its live status and duration, then the answer. Extras: starter
// prompts, per-message "Send to phone", new conversation, ?q= prefill, and
// local chat retention honoring settings.chat_history.
const CHAT_KEY = 'vela-chat';
const MAX_HISTORY = 20;

// The tools the server exposes, with plain-language labels for the activity
// list.
const TOOLS = [
  { name: 'list_apps', label: 'Listing apps', icon: SquaresFour },
  { name: 'app_status', label: 'Checking app status', icon: Pulse },
  { name: 'app_logs', label: 'Reading logs', icon: Scroll },
  { name: 'engine_status', label: 'Checking engine', icon: HardDrives },
];

const STARTERS = [
  ['Which apps are running?', Play],
  ['Help me investigate an app problem', Scroll],
  ['What can you see?', Eye],
];

function toolMeta(name) {
  return TOOLS.find((t) => t.name === name) ?? { name, label: 'Working', icon: Wrench };
}

function loadChat() {
  try {
    const raw = JSON.parse(localStorage.getItem(CHAT_KEY) || '[]');
    if (Array.isArray(raw)) {
      return raw
        .filter(
          (m) =>
            m && (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string',
        )
        .slice(-MAX_HISTORY);
    }
  } catch {
    // Corrupted or unavailable storage; start fresh.
  }
  return [];
}

function clearChat() {
  try {
    localStorage.removeItem(CHAT_KEY);
  } catch {
    // Storage unavailable; nothing to wipe.
  }
}

function CopyMessage({ message }) {
  const [state, setState] = useState('Copy');
  useEffect(() => {
    if (state === 'Copy') return;
    const timer = setTimeout(() => setState('Copy'), 2500);
    return () => clearTimeout(timer);
  }, [state]);
  return (
    <button
      type="button"
      className="msg-push"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(message);
          setState('Copied');
        } catch {
          setState('Could not copy');
        }
      }}
    >
      <Copy size={14} aria-hidden />
      <span aria-live="polite">{state}</span>
    </button>
  );
}

function SendToPhone({ message }) {
  const [state, setState] = useState('idle'); // idle | sending | sent | error
  const [error, setError] = useState('');
  useEffect(() => {
    if (state !== 'sent' && state !== 'error') return;
    const t = setTimeout(() => setState('idle'), 2500);
    return () => clearTimeout(t);
  }, [state]);
  return (
    <button
      type="button"
      className="msg-push"
      disabled={state === 'sending'}
      onClick={() => {
        setState('sending');
        sendToPhone({ title: 'From Vela Ask', message })
          .then(() => setState('sent'))
          .catch((e) => {
            setError(e instanceof Error ? e.message : 'Send failed');
            setState('error');
          });
      }}
    >
      {state === 'sending' ? (
        'Sending…'
      ) : state === 'sent' ? (
        'Sent ✓'
      ) : state === 'error' ? (
        error
      ) : (
        <>
          <Broadcast size={14} aria-hidden /> Send to phone
        </>
      )}
    </button>
  );
}

// The tools a turn reached for, exactly as they ran: name, live status,
// wall-clock duration.
function ToolCalls({ activities, live = false }) {
  if (!activities.length) return null;
  const failed = activities.some((activity) => activity.state === 'error');
  return (
    <details className="tool-details" open={live ? true : undefined}>
      <summary>
        {live
          ? 'Checking your server'
          : `${activities.length} ${activities.length === 1 ? 'check' : 'checks'}${failed ? ' · some unavailable' : ' completed'}`}
      </summary>
      <ol className="tool-calls" aria-label="Tool activity" aria-live="polite">
        {activities.map((a) => (
          <ToolCall key={a.id} activity={a} />
        ))}
      </ol>
    </details>
  );
}

function ToolCall({ activity }) {
  const meta = toolMeta(activity.tool);
  const IconCmp = meta.icon;
  // Sub-100ms calls report "Done": a rounded "0.0s" reads as a broken timer.
  const elapsed = activity.ended ? activity.ended - activity.started : null;
  const seconds = elapsed !== null && elapsed >= 100 ? `${(elapsed / 1000).toFixed(1)}s` : null;
  return (
    <li className="tool-call" data-state={activity.state}>
      <span className="tool-glyph" aria-hidden>
        <IconCmp size={13} />
      </span>
      <span className="tool-name">
        <span className="mono">{activity.tool}</span>
        <span className="tool-label">{meta.label}</span>
      </span>
      <span className="tool-status">
        {activity.state === 'running' && (
          <>
            <CircleNotch size={12} className="spin" aria-hidden />
            Working…
          </>
        )}
        {activity.state === 'complete' && (
          <>
            <CheckCircle size={12} aria-hidden />
            {seconds ?? 'Done'}
          </>
        )}
        {activity.state === 'error' && (
          <>
            <Warning size={12} aria-hidden />
            Unavailable
          </>
        )}
      </span>
    </li>
  );
}

export default function Ask() {
  const [settings, setSettings] = useState(null);
  const [ai, setAi] = useState(null);
  const [aiFailed, setAiFailed] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [params, setParams] = useSearchParams();
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [stream, setStream] = useState('');
  const [error, setError] = useState(null);
  const [activities, setActivities] = useState([]);
  const [atBottom, setAtBottom] = useState(true);
  const logRef = useRef(null);
  const followRef = useRef(true);
  const controller = useRef(null);
  const conversation = useRef(undefined);
  const loaded = useRef(false);
  const prefilled = useRef(false);

  async function reconnect() {
    setReconnecting(true);
    try {
      setAi(await getAiStatus());
      setAiFailed(false);
    } catch {
      setAiFailed(true);
    } finally {
      setReconnecting(false);
    }
  }

  function jumpToLatest() {
    followRef.current = true;
    setAtBottom(true);
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }

  useEffect(() => {
    if (followRef.current) jumpToLatest();
  }, [messages, stream, activities, busy, error]);

  useEffect(() => {
    const observer = new ResizeObserver(() => {
      if (followRef.current) jumpToLatest();
    });
    observer.observe(logRef.current);
    observer.observe(logRef.current.firstElementChild);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let live = true;
    getSettings()
      .then((s) => live && setSettings(s))
      .catch(() => live && setSettings({}));
    getAiStatus()
      .then((s) => live && setAi(s))
      .catch(() => live && setAiFailed(true));
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => () => controller.current?.abort(), []);

  // Chat retention follows settings.chat_history: turning it off wipes the
  // stored transcript immediately; when on, the last turns are restored once.
  useEffect(() => {
    if (!settings) return;
    if (settings.chat_history === false) clearChat();
    if (!loaded.current) {
      loaded.current = true;
      if (settings.chat_history !== false) setMessages(loadChat());
    }
  }, [settings]);

  // A pre-filled question can arrive as /ask?q=… — prefill the composer
  // rather than sending automatically: the user controls what is sent.
  useEffect(() => {
    if (prefilled.current) return;
    const q = params.get('q');
    if (!q) return;
    prefilled.current = true;
    setParams({}, { replace: true });
    setInput(q.slice(0, 4000));
  }, [params, setParams]);

  async function ask(text, retryIndex) {
    text = text.trim();
    if (!text || text.length > 4000 || controller.current || !settings || !ai?.reachable) return;
    followRef.current = true;
    setBusy(true);
    if (retryIndex === undefined) setInput('');
    setStream('');
    setError(null);
    setActivities([]);
    const next =
      retryIndex === undefined
        ? [...messages, { role: 'user', content: text }]
        : messages.slice(0, retryIndex + 1);
    setMessages(next);
    const ac = new AbortController();
    controller.current = ac;
    let final = '';
    let complete = false;
    let streamError = '';
    let turnTools = [];
    function save(updated) {
      setMessages(updated);
      if (settings?.chat_history !== false) {
        try {
          localStorage.setItem(CHAT_KEY, JSON.stringify(updated.slice(-MAX_HISTORY)));
        } catch {
          /* Storage unavailable. */
        }
      }
    }
    try {
      await streamChat({
        message: text,
        conversationId: conversation.current,
        signal: ac.signal,
        onEvent: (event) => {
          if (event.error) streamError = event.error;
          if (event.conversationId) conversation.current = event.conversationId;
          if (typeof event.text === 'string') {
            // Tolerate both cumulative snapshots and per-token deltas.
            final = final && event.text.startsWith(final) ? event.text : final + event.text;
            setStream(final);
          }
          if (event.done) complete = true;
          if (event.activity) {
            // Keep the first-seen timestamp so a finished call can report how
            // long it actually took, rather than a number we made up.
            const now = Date.now();
            const prior = turnTools.find((a) => a.id === event.activity.id);
            const entry = {
              id: event.activity.id,
              tool: event.activity.tool,
              state: event.activity.state,
              started: prior?.started ?? now,
              ended: event.activity.state === 'running' ? undefined : now,
            };
            turnTools = [...turnTools.filter((a) => a.id !== entry.id), entry];
            setActivities(turnTools);
          }
        },
      });
      if (streamError) throw new Error(streamError);
      if (!complete)
        throw new Error('Connection ended before the answer finished. Please try again.');
      const updated = [...next, { role: 'assistant', content: final, tools: turnTools }];
      save(updated);
    } catch (e) {
      const settled = turnTools.map((activity) =>
        activity.state === 'running'
          ? { ...activity, state: 'error', ended: Date.now() }
          : activity,
      );
      save([...next, { role: 'assistant', content: final, tools: settled, interrupted: true }]);
      setError(
        ac.signal.aborted ? 'Response stopped.' : e instanceof Error ? e.message : 'Chat failed',
      );
    } finally {
      controller.current = null;
      setBusy(false);
      setStream('');
      setActivities([]);
    }
  }

  function reset() {
    conversation.current = undefined;
    setMessages([]);
    setActivities([]);
    setError(null);
    setInput('');
    jumpToLatest();
    clearChat();
  }

  const aiOffline = aiFailed || (ai && !ai.reachable);
  const model = ai?.models?.[0];
  const lastQuestionIndex = messages.findLastIndex((message) => message.role === 'user');
  const canSend = Boolean(settings && ai?.reachable);

  return (
    <div className="ask-workspace">
      <header className="page-head-row ask-header">
        <div>
          <h1 className="page-title">Ask</h1>
          <p className="ask-status">
            <span
              className={`dot${ai?.reachable ? ' dot-ok' : aiOffline ? ' dot-bad' : ''}`}
              aria-hidden
            />
            <span>
              {ai?.reachable
                ? 'Connected to your server'
                : aiOffline
                  ? 'Assistant unavailable'
                  : 'Connecting to assistant…'}
            </span>
          </p>
        </div>
        <button className="btn" onClick={reset} disabled={busy}>
          <Plus size={16} aria-hidden /> New conversation
        </button>
      </header>

      {aiOffline && (
        <div className="banner banner-error ask-banner" role="alert">
          <div>
            <strong>The assistant is offline.</strong>
            <p>
              Vela can't reach the configured model server. You can keep browsing, but questions
              won't be answered until it's back.
            </p>
          </div>
          <button className="btn" onClick={reconnect} disabled={reconnecting}>
            {reconnecting ? 'Checking…' : 'Reconnect'}
          </button>
        </div>
      )}

      <AskContext />

      <div className="chat-stage">
        <div
          className="chat-log"
          ref={logRef}
          role="region"
          aria-label="Conversation"
          tabIndex={0}
          onScroll={() => {
            const log = logRef.current;
            const pinned = log.scrollHeight - log.scrollTop - log.clientHeight < 64;
            followRef.current = pinned;
            setAtBottom(pinned);
          }}
        >
          <div className={`chat-transcript${!messages.length ? ' chat-transcript-empty' : ''}`}>
            {!messages.length && !busy && (
              <div className="chat-intro">
                <div className="chat-intro-mark" aria-hidden>
                  <Sparkle size={28} />
                </div>
                <span className="chat-eyebrow">Your server, in conversation</span>
                <h2>What should I look into?</h2>
                <p>
                  Check on your apps, make sense of a log, or find out why something stopped.
                  Mention an app with @ to get specific.
                </p>
                <div className="chat-starters">
                  {STARTERS.map(([text, IconCmp]) => (
                    <button type="button" key={text} onClick={() => setInput(text)}>
                      <IconCmp size={15} aria-hidden />
                      <span>{text}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((m, i) => (
              <div className="chat-turn" key={i}>
                {m.role === 'user' ? (
                  <div className="chat-user">
                    <div className="chat-bubble">{m.content}</div>
                  </div>
                ) : (
                  <>
                    <ToolCalls activities={m.tools ?? []} />
                    <div className="chat-assistant">
                      <span className="chat-avatar" aria-hidden>
                        <Sparkle size={12} />
                      </span>
                      <div className="chat-text">
                        <span className="chat-author">Vela</span>
                        <ChatMarkdown>{m.content}</ChatMarkdown>
                        {m.interrupted && (
                          <span className="chat-interrupted">Response incomplete</span>
                        )}
                        {m.content && (
                          <div className="message-actions">
                            <CopyMessage message={m.content} />
                            <SendToPhone message={m.content} />
                          </div>
                        )}
                      </div>
                    </div>
                  </>
                )}
              </div>
            ))}

            <ToolCalls activities={activities} live />

            {busy && (
              <div className="chat-assistant" aria-busy="true">
                <span className="chat-avatar" aria-hidden>
                  <Sparkle size={12} />
                </span>
                <div className="chat-text">
                  <span className="chat-author">
                    Vela{' '}
                    <span className="chat-stream-label" role="status">
                      Responding…
                    </span>
                  </span>
                  {stream ? (
                    <ChatMarkdown>{stream}</ChatMarkdown>
                  ) : (
                    <span role="status">
                      {activities.some((a) => a.state === 'running')
                        ? 'Checking the hub…'
                        : 'Preparing response…'}
                    </span>
                  )}
                  <span className="stream-caret" aria-hidden />
                </div>
              </div>
            )}

            {error && (
              <div className="chat-error" role="alert">
                <span>{error}</span>
                {lastQuestionIndex >= 0 && (
                  <button
                    type="button"
                    className="btn"
                    disabled={busy || !canSend}
                    onClick={() => ask(messages[lastQuestionIndex].content, lastQuestionIndex)}
                  >
                    <ArrowClockwise size={14} aria-hidden />
                    Try again
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
        {!atBottom && (
          <button className="chat-jump btn" onClick={jumpToLatest}>
            <ArrowDown size={15} aria-hidden />
            Jump to latest
          </button>
        )}
      </div>
      <ChatComposer
        input={input}
        setInput={setInput}
        busy={busy}
        disabled={!canSend}
        onSend={ask}
        onStop={() => controller.current?.abort()}
        model={model}
      />
    </div>
  );
}
