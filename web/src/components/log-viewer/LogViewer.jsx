import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../api.js';
import { useResource } from '../../hooks/useResource.js';
import { useApps } from '../../store.jsx';
import Button from '../ui/Button.jsx';
import Dialog from '../ui/Dialog.jsx';
import EmptyState from '../ui/EmptyState.jsx';
import LoadingState from '../ui/LoadingState.jsx';
import LogContent from './LogContent.jsx';
import LogFileList from './LogFileList.jsx';
import LogToolbar from './LogToolbar.jsx';

const LIVE_INTERVAL = 3000;

// The whole viewer: which logs exist, the lines of the selected one, and the
// four things a person does with them — search, follow, download, clear.
// Structure from ServerKit's `LogViewer.jsx` (MIT, same owner).
export default function LogViewer({ selected, onSelect }) {
  const { pushToast } = useApps();
  const [lines, setLines] = useState(200);
  const [pattern, setPattern] = useState('');
  const [live, setLive] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const searchRef = useRef(null);
  const contentRef = useRef(null);
  const cancelRef = useRef(null);

  const { data: listing, error: listError, refresh: refreshList } = useResource(api.getLogs);
  const logs = listing?.logs || null;

  // Whatever the URL asked for, only a log that exists can be shown. The first
  // one is the fallback so the tab is never blank when there are logs.
  const active = useMemo(() => {
    if (!logs || logs.length === 0) return null;
    return logs.some((log) => log.name === selected) ? selected : logs[0].name;
  }, [logs, selected]);

  // A search is a different request from a tail, so the debounce lives here
  // rather than in the loader: typing must not fire one request per keystroke.
  const [debounced, setDebounced] = useState('');
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(pattern.trim()), 250);
    return () => clearTimeout(timer);
  }, [pattern]);

  const load = useCallback(
    (options) =>
      active ? api.readLog(active, { lines, pattern: debounced }, options) : Promise.resolve(null),
    [active, lines, debounced],
  );
  const {
    data: body,
    error: readError,
    loading,
    refresh,
  } = useResource(load, {
    enabled: Boolean(active),
    intervalMs: live ? LIVE_INTERVAL : 0,
  });

  // `/` focuses search and `End` jumps to the newest line, the two things a
  // keyboard reader wants most in a log.
  useEffect(() => {
    const onKey = (event) => {
      const target = event.target;
      const typing =
        target instanceof HTMLElement &&
        (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);
      if (event.key === '/' && !typing && !event.metaKey && !event.ctrlKey) {
        event.preventDefault();
        searchRef.current?.focus();
      }
      if (event.key === 'End' && !typing && contentRef.current) {
        contentRef.current.scrollTop = contentRef.current.scrollHeight;
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  const download = async () => {
    if (!active) return;
    setDownloading(true);
    try {
      const blob = await api.downloadLog(active);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = active;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      pushToast(error.message || `Could not download ${active}.`);
    } finally {
      setDownloading(false);
    }
  };

  const clear = async () => {
    if (!active) return;
    setClearing(true);
    try {
      await api.clearLog(active);
      setConfirming(false);
      pushToast(`${active} cleared.`, 'success');
      await Promise.all([refresh(), refreshList()]);
    } catch (error) {
      pushToast(error.message || `Could not clear ${active}.`);
    } finally {
      setClearing(false);
    }
  };

  if (listError) {
    return (
      <EmptyState title="Logs are unavailable">
        {listError.message} <Button onClick={refreshList}>Try again</Button>
      </EmptyState>
    );
  }

  if (!logs) return <LoadingState>Looking for logs…</LoadingState>;

  if (logs.length === 0) {
    return (
      <EmptyState title="No logs yet">
        Vela writes a log while it runs, and one for each app it starts. There is nothing here until
        something has run.
      </EmptyState>
    );
  }

  const searching = Boolean(debounced);
  const shown = body?.lines || [];

  return (
    <div className="logs-layout">
      <LogFileList logs={logs} selected={active} onSelect={onSelect} />

      <section className="logs-viewer">
        <LogToolbar
          searchRef={searchRef}
          pattern={pattern}
          onPattern={setPattern}
          lines={lines}
          onLines={setLines}
          live={live}
          onLive={setLive}
          onRefresh={refresh}
          onDownload={download}
          onClear={() => setConfirming(true)}
          downloading={downloading}
          clearing={clearing}
          disabled={!active}
        />

        <p className="logs-status panel-note" aria-live="polite">
          {readError
            ? readError.message
            : searching
              ? `${body?.total ?? 0} matching line${body?.total === 1 ? '' : 's'}${
                  body?.truncated ? `, showing the first ${shown.length}` : ''
                }`
              : `${body?.total ?? 0} line${body?.total === 1 ? '' : 's'}${
                  body?.truncated ? `, showing the last ${shown.length}` : ''
                }`}
        </p>

        <LogContent
          ref={contentRef}
          lines={shown}
          loading={loading}
          pattern={debounced}
          scrollKey={`${active}:${debounced}:${lines}`}
          empty={searching ? 'Nothing in this log matches that.' : 'This log is empty.'}
        />
      </section>

      <Dialog
        open={confirming}
        onClose={() => setConfirming(false)}
        pending={clearing}
        initialFocusRef={cancelRef}
      >
        <h2>Clear {active}?</h2>
        <p className="panel-note">
          The lines in this log are deleted from this computer. Vela keeps writing to the same file,
          so new activity still appears. Rotated copies are not touched.
        </p>
        <div className="actions">
          <Button ref={cancelRef} onClick={() => setConfirming(false)} disabled={clearing}>
            Cancel
          </Button>
          <Button variant="danger" pending={clearing} onClick={clear}>
            Clear log
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
