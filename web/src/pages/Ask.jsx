import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
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
  SidebarSimple,
  Sparkle,
  SquaresFour,
  Warning,
  Wrench,
} from '@phosphor-icons/react';
import {
  createConversation,
  createRoom,
  deleteConversation,
  getAiStatus,
  getSettings,
  importLegacyChat,
  listConversations,
  readConversation,
  sendToPhone,
  streamChat,
  updateConversation,
} from '../chatApi.js';
import { api } from '../api.js';
import {
  createBot,
  deleteBot,
  duplicateBot,
  listBots,
  setRoomMembers,
  updateBot,
} from '../botsApi.js';
import ChatComposer from '../components/ChatComposer.jsx';
import ConversationPanel from '../components/ConversationPanel.jsx';
import NavDrawer from '../components/NavDrawer.jsx';
import WorkspacePage from '../components/WorkspacePage.jsx';
import BotIcon from '../components/bots/BotIcon.jsx';
import BotEditor from '../components/bots/BotEditor.jsx';
import RoomDialog from '../components/bots/RoomDialog.jsx';
import { readJson, readLocal, removeLocal, writeLocal } from '../storage.js';
import { copyText } from '../clipboard.js';
import { SPLIT as NARROW } from '../breakpoints.js';
const Markdown = lazy(() => import('../components/ChatMarkdown.jsx'));

function ChatMarkdown({ children }) {
  return (
    <Suspense fallback={<div className="chat-markdown">{children}</div>}>
      <Markdown>{children}</Markdown>
    </Suspense>
  );
}

// Ask: streaming local chat (POST /api/chat, SSE) inside the shared workspace.
// Conversations are stored on the server when chat history is enabled; the
// browser keeps only presentation state. The legacy browser-held transcript is
// imported once, guarded by a server-side marker.
const LEGACY_KEY = 'vela-chat';
const PANEL_KEY = 'vela.ask.panel.v1';
const TAB_KEY = 'vela.ask.tab.v1';
const DRAFT_DEBOUNCE_MS = 700;

// One send gets one id. Retrying the same send reuses it, so a dropped
// connection cannot produce a second set of answers.
function newRequestId() {
  return globalThis.crypto?.randomUUID?.() ?? `req-${Date.now()}-${Math.random()}`;
}

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

function legacyTranscript() {
  const raw = readJson(LEGACY_KEY, null);
  if (!Array.isArray(raw)) return null;
  return raw.filter(
    (m) => m && (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string',
  );
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
        setState((await copyText(message)) ? 'Copied' : 'Could not copy');
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
  const elapsed = activity.ms ?? (activity.ended ? activity.ended - activity.started : null);
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
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const [settings, setSettings] = useState(null);
  const historyEnabled = settings ? settings.chat_history !== false : true;
  const settingsReady = Boolean(settings);
  const historyRef = useRef(true);
  historyRef.current = historyEnabled;
  const [ai, setAi] = useState(null);
  const [aiFailed, setAiFailed] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [params, setParams] = useSearchParams();

  // Ask greets by name when the user gave one. It is read here rather than
  // threaded through the shell, and the greeting simply drops the name when
  // there is none rather than inventing "there".
  const [displayName, setDisplayName] = useState('');
  useEffect(() => {
    let cancelled = false;
    api
      .getSettings()
      .then((data) => {
        if (!cancelled) setDisplayName(data?.identity?.displayName || '');
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const [messages, setMessages] = useState([]);
  const [title, setTitle] = useState('New conversation');
  const [loadError, setLoadError] = useState('');
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [stream, setStream] = useState('');
  const [error, setError] = useState(null);
  const [activities, setActivities] = useState([]);
  const [atBottom, setAtBottom] = useState(true);

  const [conversations, setConversations] = useState([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState('');
  const [query, setQuery] = useState('');
  const [showArchived, setShowArchived] = useState(false);

  // Bots and rooms. `conversation` holds the kind/bot binding for whatever is
  // open, so the transcript and composer know who they are talking to.
  const [tab, setTab] = useState(() => readLocal(TAB_KEY, 'chats'));
  const [builtin, setBuiltin] = useState(null);
  const [bots, setBots] = useState([]);
  const [botsLoading, setBotsLoading] = useState(true);
  const [botsError, setBotsError] = useState('');
  const [editingBot, setEditingBot] = useState(null);
  const [roomDialog, setRoomDialog] = useState(null);
  const [conversation, setConversation] = useState(null);
  // Live per-responder state during a room run, keyed by bot id.
  const [responders, setResponders] = useState([]);
  // Which bot a brand-new chat will be bound to, before it exists on the server.
  const [pendingBot, setPendingBot] = useState('vela');
  // The panel is a column on wide screens. Below 1100px it moves into the
  // navigation drawer beside the rail, opened on demand from the header, so it
  // starts closed there rather than covering the conversation.
  const [narrow, setNarrow] = useState(() => matchMedia(NARROW).matches);
  const [panelOpen, setPanelOpen] = useState(
    () => readLocal(PANEL_KEY, 'open') !== 'closed' && !matchMedia(NARROW).matches,
  );

  const logRef = useRef(null);
  const followRef = useRef(true);
  const controller = useRef(null);
  const prefilled = useRef(false);
  const migrated = useRef(false);
  const draftTimer = useRef(null);
  const draftFor = useRef(null);
  // The route change that happens when the first question creates a
  // conversation is this view's own; it must not cancel that same request or
  // reload the transcript underneath it.
  const selfNavigated = useRef(null);
  // Which conversation the view is showing right now. A request that finishes
  // after the reader moved on must not write into whatever is on screen.
  const activeId = useRef(conversationId);
  // Which conversation the transcript effect last handled, so a settings change
  // never looks like a route change and discards what is being typed.
  const loadedFor = useRef(conversationId);

  const refreshBots = useCallback(async (signal) => {
    try {
      const data = await listBots({ signal });
      setBuiltin(data.builtin);
      setBots(data.bots || []);
      setBotsError('');
    } catch (failure) {
      if (failure?.name !== 'AbortError')
        setBotsError(failure instanceof Error ? failure.message : 'Could not load bots.');
    } finally {
      setBotsLoading(false);
    }
  }, []);

  const refreshList = useCallback(
    async (signal) => {
      if (!historyEnabled) {
        setConversations([]);
        setListLoading(false);
        return;
      }
      try {
        const data = await listConversations({ query, archived: showArchived, signal });
        setConversations(data.conversations || []);
        setListError('');
      } catch (failure) {
        if (failure?.name !== 'AbortError')
          setListError(failure instanceof Error ? failure.message : 'Could not load history.');
      } finally {
        setListLoading(false);
      }
    },
    [historyEnabled, query, showArchived],
  );

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

  const jumpToLatest = useCallback(() => {
    followRef.current = true;
    setAtBottom(true);
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, []);

  useEffect(() => {
    if (followRef.current) jumpToLatest();
  }, [messages, stream, activities, busy, error, jumpToLatest]);

  useEffect(() => {
    const observer = new ResizeObserver(() => {
      if (followRef.current) jumpToLatest();
    });
    observer.observe(logRef.current);
    observer.observe(logRef.current.firstElementChild);
    return () => observer.disconnect();
  }, [jumpToLatest]);

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

  // Bots load independently of chat history: they are settings, and stay
  // available when retention is off.
  useEffect(() => {
    const ac = new AbortController();
    refreshBots(ac.signal);
    return () => ac.abort();
  }, [refreshBots]);

  useEffect(() => writeLocal(TAB_KEY, tab), [tab]);

  useEffect(() => {
    const update = (event) => setSettings((current) => ({ ...current, ...event.detail }));
    window.addEventListener('vela:chat-settings', update);
    return () => window.removeEventListener('vela:chat-settings', update);
  }, []);

  // One-time import of the transcript the browser used to hold. The server
  // records the marker, so a second tab or a retry cannot duplicate it, and
  // nothing is imported while retention is off.
  useEffect(() => {
    if (!settings || !historyEnabled || migrated.current) return;
    migrated.current = true;
    const legacy = legacyTranscript();
    if (!legacy) return;
    importLegacyChat(legacy.slice(-100))
      .then((result) => {
        // Storage unavailable is not a problem here: the server marker is
        // what prevents a repeat.
        removeLocal(LEGACY_KEY);
        if (result?.imported) refreshList();
      })
      .catch(() => {
        migrated.current = false;
      });
  }, [settings, historyEnabled, refreshList]);

  // Retention turned off: stop showing a history that is no longer stored.
  useEffect(() => {
    if (!settings || historyEnabled) return;
    setConversations([]);
    setListLoading(false);
    removeLocal(LEGACY_KEY);
  }, [settings, historyEnabled]);

  useEffect(() => {
    if (!settings) return undefined;
    const ac = new AbortController();
    refreshList(ac.signal);
    return () => ac.abort();
  }, [settings, refreshList]);

  // Selecting another conversation cancels the request in flight rather than
  // letting an invisible one keep writing. The partial answer is kept with the
  // conversation it belonged to.
  useEffect(() => {
    activeId.current = conversationId;
  }, [conversationId]);

  useEffect(() => {
    if (selfNavigated.current && selfNavigated.current === conversationId) {
      selfNavigated.current = null;
      loadedFor.current = conversationId;
      return undefined;
    }
    const changed = loadedFor.current !== conversationId;
    loadedFor.current = conversationId;
    if (changed) controller.current?.abort();
    setStream('');
    setActivities([]);
    setError(null);
    setLoadError('');
    setResponders([]);
    if (!conversationId) {
      setMessages([]);
      setConversation(null);
      setTitle('New conversation');
      // Only a real move to another conversation discards the draft.
      if (changed) setInput('');
      return undefined;
    }
    if (!settingsReady) return undefined;
    if (!historyRef.current) {
      // Retention is off, so no stored conversation exists behind this route.
      navigate('/ask', { replace: true });
      return undefined;
    }
    const ac = new AbortController();
    readConversation(conversationId, { signal: ac.signal })
      .then((data) => {
        setMessages(data.messages || []);
        setConversation(data);
        setTitle(data.title);
        draftFor.current = conversationId;
        setInput(data.draft || '');
        jumpToLatest();
      })
      .catch((failure) => {
        if (failure?.name === 'AbortError') return;
        setMessages([]);
        setLoadError(
          failure?.status === 404 || /not found/i.test(failure?.message || '')
            ? 'That conversation is no longer available.'
            : failure.message,
        );
      });
    return () => ac.abort();
  }, [conversationId, settingsReady, jumpToLatest, navigate]);

  // Turning retention off removes the stored conversation behind this route.
  useEffect(() => {
    if (settings && !historyEnabled && conversationId) navigate('/ask', { replace: true });
  }, [settings, historyEnabled, conversationId, navigate]);

  // A pre-filled question can arrive as /ask?q=… — prefill the composer rather
  // than sending automatically: the user controls what is sent.
  useEffect(() => {
    if (prefilled.current) return;
    const q = params.get('q');
    if (!q) return;
    prefilled.current = true;
    setParams({}, { replace: true });
    setInput(q.slice(0, 4000));
  }, [params, setParams]);

  // Only the desktop column remembers its state; the drawer always starts closed.
  useEffect(() => {
    if (!narrow) writeLocal(PANEL_KEY, panelOpen ? 'open' : 'closed');
  }, [panelOpen, narrow]);

  // Crossing the breakpoint must not leave a drawer open over the transcript
  // or hide the column the wide layout expects.
  useEffect(() => {
    const query = matchMedia(NARROW);
    const update = () => {
      setNarrow(query.matches);
      setPanelOpen(query.matches ? false : readLocal(PANEL_KEY, 'open') !== 'closed');
    };
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);

  const saveDraft = useCallback(
    (value) => {
      if (!historyEnabled || !conversationId) return;
      clearTimeout(draftTimer.current);
      draftTimer.current = setTimeout(() => {
        updateConversation(conversationId, { draft: value.slice(0, 4000) }).catch(() => {});
      }, DRAFT_DEBOUNCE_MS);
    },
    [conversationId, historyEnabled],
  );

  const changeInput = useCallback(
    (value) => {
      const next = typeof value === 'function' ? value(input) : value;
      setInput(next);
      saveDraft(next);
    },
    [input, saveDraft],
  );

  // Flush a pending draft before this view stops owning it.
  useEffect(() => () => clearTimeout(draftTimer.current), []);

  async function ask(text, retryIndex, options = {}) {
    text = text.trim();
    if (!text || text.length > 4000 || controller.current || !settings || !ai?.reachable) return;
    followRef.current = true;
    setBusy(true);
    if (retryIndex === undefined && !options.only) setInput('');
    setStream('');
    setError(null);
    setActivities([]);
    setResponders([]);

    let target = conversationId;
    if (historyEnabled && !target) {
      try {
        // A new chat is bound to whichever bot the composer is pointed at.
        target = (await createConversation({ kind: 'direct', botId: pendingBot })).id;
      } catch (failure) {
        setBusy(false);
        setError(failure instanceof Error ? failure.message : 'Could not start a conversation.');
        return;
      }
      selfNavigated.current = target;
      activeId.current = target;
      navigate(`/ask/${target}`, { replace: true });
    }

    const room = conversation?.kind === 'room';
    // The id survives a retry of the same send, which is what stops a network
    // hiccup from producing two answers.
    const requestId = options.requestId ?? newRequestId();
    // Bind this request to one conversation; a later switch aborts it, and
    // nothing it produces may be applied to a different one.
    const boundTo = target;
    const stillShowing = () => (activeId.current ?? null) === (boundTo ?? null);
    if (historyEnabled && target) {
      clearTimeout(draftTimer.current);
      updateConversation(target, { draft: '' }).catch(() => {});
    }

    const next = options.only
      ? messages
      : retryIndex === undefined
        ? [...messages, { role: 'user', content: text, senderKind: 'user' }]
        : messages.slice(0, retryIndex + 1);
    setMessages(next);
    const ac = new AbortController();
    controller.current = ac;
    let final = '';
    let complete = false;
    let streamError = '';
    let turnTools = [];
    // Room answers accumulate per responder and are appended as each finishes,
    // so an earlier bot's reply stays on screen while a later one is writing.
    let settled = [...next];
    let currentBot = null;
    try {
      await streamChat({
        message: text,
        conversationId: boundTo ?? null,
        requestId,
        recipients: options.recipients ?? [],
        only: options.only ?? [],
        signal: ac.signal,
        onEvent: (event) => {
          // A frame that names a different conversation can never be applied
          // to the one on screen.
          if (!stillShowing()) return;
          if (event.conversationId && boundTo && event.conversationId !== boundTo) return;
          if (event.error && !event.botId) streamError = event.error;
          if (event.title) setTitle(event.title);

          if (event.room?.responders) {
            setResponders(event.room.responders.map((r) => ({ ...r, text: '', error: '' })));
            return;
          }

          if (room && event.botId) {
            // Attribution is per frame, so nothing a bot streams can be shown
            // under another bot's name.
            if (event.state === 'responding') {
              currentBot = { ...event, text: '' };
              final = '';
            }
            if (typeof event.text === 'string' && currentBot?.botId === event.botId) {
              final = final && event.text.startsWith(final) ? event.text : final + event.text;
              currentBot = { ...currentBot, text: final };
              setStream(final);
            }
            if (
              event.state === 'complete' ||
              event.state === 'failed' ||
              event.state === 'stopped'
            ) {
              if (event.state === 'complete' && final) {
                settled = [
                  ...settled,
                  {
                    role: 'assistant',
                    senderKind: 'bot',
                    content: final,
                    botId: event.botId,
                    botName: event.botName,
                    model: event.model,
                    tools: turnTools,
                    state: 'complete',
                  },
                ];
                setMessages(settled);
              } else if (event.state !== 'complete') {
                settled = [
                  ...settled,
                  {
                    role: 'assistant',
                    senderKind: 'bot',
                    content: final,
                    botId: event.botId,
                    botName: event.botName,
                    model: event.model,
                    tools: turnTools,
                    state: event.state,
                    error: event.error,
                    interrupted: true,
                  },
                ];
                setMessages(settled);
              }
              final = '';
              turnTools = [];
              currentBot = null;
              setStream('');
              setActivities([]);
            }
            setResponders((current) =>
              current.map((r) =>
                r.botId === event.botId
                  ? { ...r, state: event.state ?? r.state, error: event.error ?? r.error }
                  : r,
              ),
            );
            if (event.activity) {
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
            return;
          }

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
      // A room ends when its last responder settles; there is no single `done`.
      if (!complete && !room)
        throw new Error('Connection ended before the answer finished. Please try again.');
      if (stillShowing() && !room)
        setMessages([
          ...next,
          {
            role: 'assistant',
            senderKind: 'bot',
            content: final,
            tools: turnTools,
            botId: conversation?.botId ?? pendingBot,
            botName: botName(conversation?.botId ?? pendingBot),
            model: ai?.chat_model,
            state: 'complete',
          },
        ]);
    } catch (e) {
      const stopped = turnTools.map((activity) =>
        activity.state === 'running'
          ? { ...activity, state: 'error', ended: Date.now() }
          : activity,
      );
      if (stillShowing()) {
        if (room) {
          // Whatever each bot already finished is on screen; only the one that
          // was mid-answer is marked incomplete.
          if (final)
            setMessages([
              ...settled,
              {
                role: 'assistant',
                senderKind: 'bot',
                content: final,
                tools: stopped,
                botId: currentBot?.botId,
                botName: currentBot?.botName,
                model: currentBot?.model,
                state: 'stopped',
                interrupted: true,
              },
            ]);
        } else {
          setMessages([
            ...next,
            {
              role: 'assistant',
              senderKind: 'bot',
              content: final,
              tools: stopped,
              botId: conversation?.botId ?? pendingBot,
              botName: botName(conversation?.botId ?? pendingBot),
              interrupted: true,
              state: 'stopped',
            },
          ]);
        }
        setError(
          ac.signal.aborted ? 'Response stopped.' : e instanceof Error ? e.message : 'Chat failed',
        );
      }
    } finally {
      controller.current = null;
      setBusy(false);
      setStream('');
      setActivities([]);
      setResponders([]);
      refreshList();
    }
  }

  // In the drawer, choosing something has to close it.
  const closeDrawer = () => {
    if (narrow) setPanelOpen(false);
  };

  const startNew = () => {
    controller.current?.abort();
    closeDrawer();
    setPendingBot('vela');
    navigate('/ask');
  };

  // ---- bots and rooms -------------------------------------------------

  const saveBot = async (fields) => {
    if (editingBot?.id) await updateBot(editingBot.id, fields);
    else await createBot(fields);
    await refreshBots();
  };

  const chatWithBot = (bot) => {
    // Choosing another bot starts a new chat rather than reinterpreting an old
    // transcript under different instructions.
    controller.current?.abort();
    closeDrawer();
    setPendingBot(bot.id);
    setTab('chats');
    navigate('/ask');
  };

  const duplicate = async (bot) => {
    await duplicateBot(bot.id);
    await refreshBots();
  };

  const archiveBot = async (bot) => {
    await updateBot(bot.id, { archived: !bot.archived });
    await refreshBots();
  };

  const removeBot = async (bot) => {
    await deleteBot(bot.id);
    await refreshBots();
  };

  const saveRoom = async (fields) => {
    if (roomDialog?.id) {
      const updated = await setRoomMembers(roomDialog.id, fields.botIds, fields.leadBotId);
      setConversation(updated);
      refreshList();
      return;
    }
    const created = await createRoom(fields);
    setTab('rooms');
    closeDrawer();
    refreshList();
    navigate(`/ask/${created.id}`);
  };

  const selectConversation = (conversation) => {
    closeDrawer();
    navigate(`/ask/${conversation.id}`);
  };

  const renameConversation = async (conversation, newTitle) => {
    const updated = await updateConversation(conversation.id, { title: newTitle });
    if (conversation.id === conversationId) setTitle(updated.title);
    refreshList();
  };

  const archiveConversation = async (conversation) => {
    await updateConversation(conversation.id, { archived: !conversation.archived });
    refreshList();
  };

  const removeConversation = async (conversation) => {
    await deleteConversation(conversation.id);
    if (conversation.id === conversationId) navigate('/ask', { replace: true });
    refreshList();
  };

  const aiOffline = aiFailed || (ai && !ai.reachable);
  const model = ai?.chat_model || ai?.models?.[0];
  const lastQuestionIndex = messages.findLastIndex((message) => message.role === 'user');
  const canSend = Boolean(settings && ai?.reachable);

  // Every bot the UI might need to name, including archived and deleted ones,
  // so a transcript can always render the author it recorded.
  const knownBots = new Map([
    ...(builtin ? [[builtin.id, builtin]] : []),
    ...bots.map((bot) => [bot.id, bot]),
  ]);
  const botName = (id) => knownBots.get(id)?.name ?? (id === 'vela' ? 'Vela' : 'Bot');
  const isRoom = conversation?.kind === 'room';
  const roomMembers = isRoom
    ? (conversation.botIds ?? []).map(
        (id) => knownBots.get(id) ?? { id, name: 'Removed bot', color: 'slate', missing: true },
      )
    : [];
  const availableMembers = roomMembers.filter((bot) => !bot.missing && !bot.archived);
  const roomBroken = isRoom && availableMembers.length < 2;
  const activeBot = isRoom ? null : knownBots.get(conversation?.botId ?? pendingBot);
  const botGone = !isRoom && conversation?.botId && !knownBots.has(conversation.botId);
  // Only a bot whose own answer failed can be retried, and only that one.
  const failedBots = messages
    .filter((m) => m.senderKind === 'bot' && m.state === 'failed' && m.botId)
    .map((m) => m.botId);

  const conversationPanel = (
    <ConversationPanel
      drawer={narrow}
      conversations={conversations}
      activeId={conversationId}
      loading={listLoading}
      error={listError}
      historyEnabled={historyEnabled}
      tab={tab}
      onTab={setTab}
      builtin={builtin}
      bots={bots}
      botsLoading={botsLoading}
      botsError={botsError}
      onNewBot={() => setEditingBot({})}
      onEditBot={setEditingBot}
      onDuplicateBot={duplicate}
      onArchiveBot={archiveBot}
      onDeleteBot={removeBot}
      onChatWithBot={chatWithBot}
      onNewRoom={() => setRoomDialog({})}
      query={query}
      onQuery={setQuery}
      showArchived={showArchived}
      onShowArchived={setShowArchived}
      onSelect={selectConversation}
      onNew={startNew}
      onRename={renameConversation}
      onArchive={archiveConversation}
      onDelete={removeConversation}
      onCollapse={() => setPanelOpen(false)}
      model={model}
      reachable={Boolean(ai?.reachable)}
    />
  );

  return (
    <WorkspacePage
      scroll={false}
      compactSearch
      className="ask-main"
      title={title}
      subtitle={
        !ai?.reachable
          ? aiOffline
            ? 'Assistant unavailable'
            : 'Connecting to assistant…'
          : isRoom
            ? `${roomMembers.length} bots · ${
                conversation.mode === 'roundtable' ? 'Roundtable' : 'Mention or lead'
              }`
            : `${activeBot?.name ?? 'Vela'} · ${activeBot?.model || model || 'Local model'}`
      }
      lead={
        <button
          type="button"
          className="btn btn-icon ask-panel-toggle"
          aria-label={panelOpen && !narrow ? 'Hide conversations' : 'Show conversations'}
          aria-haspopup={narrow ? 'dialog' : undefined}
          aria-expanded={narrow ? undefined : panelOpen}
          onClick={() => setPanelOpen((open) => !open)}
        >
          <SidebarSimple size={17} aria-hidden="true" />
        </button>
      }
      actions={
        <button
          type="button"
          className="btn btn-small btn-compact"
          aria-label="New conversation"
          onClick={startNew}
        >
          <Plus size={15} aria-hidden="true" />
          <span className="btn-label">New</span>
        </button>
      }
      panel={!narrow && panelOpen ? conversationPanel : null}
    >
      {narrow && panelOpen && (
        <NavDrawer
          open
          label="Conversations"
          onClose={() => setPanelOpen(false)}
          panel={conversationPanel}
        />
      )}
      <div className="ask-workspace">
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

        {loadError && (
          <div className="banner banner-error ask-banner" role="alert">
            <div>
              <strong>{loadError}</strong>
            </div>
            <button className="btn" onClick={startNew}>
              Start a new conversation
            </button>
          </div>
        )}

        {botGone && (
          <div className="banner banner-error ask-banner" role="alert">
            <div>
              <strong>This chat’s bot is no longer available.</strong>
              <p>
                Its history is kept and still shows who wrote it. Start a new chat to carry on with
                another bot — Vela will not answer as a different one here.
              </p>
            </div>
            <button className="btn" onClick={startNew}>
              New chat
            </button>
          </div>
        )}

        {roomBroken && (
          <div className="banner banner-error ask-banner" role="alert">
            <div>
              <strong>This room needs at least two available bots.</strong>
              <p>
                {roomMembers.filter((bot) => bot.missing).length
                  ? 'A bot in this room was deleted.'
                  : 'A bot in this room is archived.'}{' '}
                Change who is in the room before sending again.
              </p>
            </div>
            <button className="btn" onClick={() => setRoomDialog(conversation)}>
              Edit room
            </button>
          </div>
        )}

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
              {!messages.length && !busy && !loadError && (
                <div className="chat-intro">
                  <div className="chat-intro-mark" aria-hidden>
                    <Sparkle size={28} />
                  </div>
                  <span className="chat-eyebrow">Your server, in conversation</span>
                  <h2>
                    {displayName
                      ? `What should I look into, ${displayName}?`
                      : 'What should I look into?'}
                  </h2>
                  <p>
                    Check on your apps, make sense of a log, or find out why something stopped.
                    Mention an app with @ to get specific.
                  </p>
                  <div className="chat-starters">
                    {STARTERS.map(([text, IconCmp]) => (
                      <button type="button" key={text} onClick={() => changeInput(text)}>
                        <IconCmp size={15} aria-hidden />
                        <span>{text}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {messages.map((m, i) => (
                <div className="chat-turn" key={m.id || i}>
                  {m.role === 'user' ? (
                    <div className="chat-user">
                      <div className="chat-bubble">{m.content}</div>
                    </div>
                  ) : (
                    <>
                      <ToolCalls activities={m.tools ?? []} />
                      <div className="chat-assistant" data-state={m.state}>
                        <span className="chat-avatar" aria-hidden>
                          {knownBots.get(m.botId) ? (
                            <BotIcon bot={knownBots.get(m.botId)} size={12} />
                          ) : (
                            <Sparkle size={12} />
                          )}
                        </span>
                        <div className="chat-text">
                          <span className="chat-author">
                            {m.botName || botName(m.botId)}
                            {/* Only a model that was actually recorded is shown. */}
                            {m.model && <span className="chat-author-model">{m.model}</span>}
                          </span>
                          <ChatMarkdown>{m.content}</ChatMarkdown>
                          {m.state === 'failed' && (
                            <span className="chat-failed" role="alert">
                              {m.error || 'This bot could not answer.'}
                            </span>
                          )}
                          {m.state === 'stopped' && (
                            <span className="chat-interrupted">Stopped</span>
                          )}
                          {m.interrupted && m.state !== 'failed' && m.state !== 'stopped' && (
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

              {busy && responders.length > 1 && (
                <ul className="room-queue" aria-label="Who is answering">
                  {responders.map((responder) => (
                    <li key={responder.botId} data-state={responder.state}>
                      {knownBots.get(responder.botId) && (
                        <BotIcon bot={knownBots.get(responder.botId)} size={11} />
                      )}
                      <span>{responder.name}</span>
                      <small>
                        {responder.state === 'responding'
                          ? 'Answering…'
                          : responder.state === 'complete'
                            ? 'Done'
                            : responder.state === 'failed'
                              ? 'Failed'
                              : responder.state === 'stopped'
                                ? 'Stopped'
                                : 'Waiting'}
                      </small>
                    </li>
                  ))}
                </ul>
              )}

              {busy && (
                <div className="chat-assistant" aria-busy="true">
                  <span className="chat-avatar" aria-hidden>
                    {(() => {
                      const speaking = responders.find((r) => r.state === 'responding');
                      const bot = knownBots.get(speaking?.botId ?? activeBot?.id);
                      return bot ? <BotIcon bot={bot} size={12} /> : <Sparkle size={12} />;
                    })()}
                  </span>
                  <div className="chat-text">
                    <span className="chat-author">
                      {responders.find((r) => r.state === 'responding')?.name ??
                        activeBot?.name ??
                        'Vela'}{' '}
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

              {/* Retrying a failed bot reruns only that bot: the ones that
                  already answered keep their replies. */}
              {!busy && isRoom && failedBots.length > 0 && lastQuestionIndex >= 0 && (
                <div className="room-retry" role="group" aria-label="Retry a bot">
                  {[...new Set(failedBots)].map((botId) => (
                    <button
                      key={botId}
                      type="button"
                      className="btn btn-small"
                      disabled={busy || !canSend}
                      onClick={() =>
                        ask(messages[lastQuestionIndex].content, undefined, {
                          only: [botId],
                          recipients: [botId],
                        })
                      }
                    >
                      <ArrowClockwise size={13} aria-hidden />
                      Retry {botName(botId)}
                    </button>
                  ))}
                </div>
              )}

              {error && (
                <div className="chat-error" role="alert">
                  <span>{error}</span>
                  {lastQuestionIndex >= 0 && !isRoom && (
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
          setInput={changeInput}
          busy={busy}
          disabled={!canSend || roomBroken || botGone}
          onSend={(text, recipients) => ask(text, undefined, { recipients })}
          onStop={() => controller.current?.abort()}
          model={activeBot?.model || model}
          bots={isRoom ? availableMembers : []}
          room={isRoom}
          mode={conversation?.mode}
          leadBotId={conversation?.leadBotId}
          botLabel={isRoom ? null : (activeBot?.name ?? 'Vela')}
        />
      </div>

      {editingBot && (
        <BotEditor
          bot={editingBot.id ? editingBot : null}
          models={ai?.models ?? []}
          defaultModel={ai?.chat_model}
          onClose={() => setEditingBot(null)}
          onSave={saveBot}
        />
      )}

      {roomDialog && (
        <RoomDialog
          bots={bots.filter((bot) => !bot.archived)}
          room={roomDialog.id ? roomDialog : null}
          onClose={() => setRoomDialog(null)}
          onSave={saveRoom}
        />
      )}
    </WorkspacePage>
  );
}
