import { useEffect, useState } from 'react';
import { CaretDown, Eye } from '@phosphor-icons/react';
import { api } from '../api.js';

// "What the assistant can see" — context transparency for the Ask page.
// Lists exactly the hub-level records the server can fold into a chat
// request. Collapsed by default with a one-line summary.
export default function AskContext() {
  const [open, setOpen] = useState(false);
  const [apps, setApps] = useState(null);
  const [engine, setEngine] = useState(null);

  useEffect(() => {
    let live = true;
    api.getApps().then((d) => live && setApps(d?.apps ?? [])).catch(() => live && setApps([]));
    api.getEngine().then((d) => live && setEngine(d)).catch(() => {});
    return () => { live = false; };
  }, []);

  const installed = (apps ?? []).filter((a) => a.installed);
  const running = installed.filter((a) => a.running);

  const summary = apps === null
    ? 'loading…'
    : `${installed.length} installed app${installed.length === 1 ? '' : 's'}, ${running.length} running, engine status, log tails`;

  return (
    <div className="ask-context">
      <button
        type="button"
        className="ask-context-toggle"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <Eye size={15} aria-hidden />
        <span>What the assistant can see</span>
        <span className="ask-context-summary">{summary}</span>
        <CaretDown size={14} aria-hidden style={{ transform: open ? 'rotate(180deg)' : undefined, transition: 'transform 140ms' }} />
      </button>
      {open && (
        <div className="ask-context-body">
          <p>
            <b>Installed apps:</b>{' '}
            {apps === null
              ? 'loading…'
              : installed.length
                ? installed.map((a) => `${a.name || a.id}${a.running ? ' (running)' : ''}`).join('; ')
                : 'none'}
          </p>
          <p>
            <b>App logs:</b> recent log lines from each installed app — enough to answer
            "why did this fail" questions.
          </p>
          <p>
            <b>Engine:</b>{' '}
            {engine
              ? `running, ${engine.apps_running ?? 0} app${engine.apps_running === 1 ? '' : 's'} up`
              : 'current engine status and version'}
          </p>
          <p className="ask-context-note">
            Data inside your apps — notes, meal plans, finance records, health data — is never
            included. Requests go to your configured Ollama server.
          </p>
        </div>
      )}
    </div>
  );
}
