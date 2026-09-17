// The files this desktop was given, and the ones it came back with.
//
// The file picker is here, in the owner's own window, and nowhere else. An
// agent never opens one: it is told which files exist, by id, and can attach
// one of those to a page that asks. That is the whole reason this panel exists
// rather than a tool called "read a file".
//
// Two other things live here because they belong to the same question of what
// this desktop keeps: whether website sign-ins survive the browser closing, and
// whether anything was sent to a website that nobody could confirm. The second
// is deliberately a person's decision — Vela will not resend it and will not
// decide on its own that it must have worked.
import { useCallback, useEffect, useRef, useState } from 'react';
import Button from '../components/ui/Button.jsx';
import { desktopsApi } from './desktopsApi.js';

/** Bytes, in the units the limits are written in. */
function size(bytes) {
  if (!Number.isFinite(bytes)) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const SOURCE_WORDS = {
  upload: 'You added this',
  download: 'Downloaded by the agent',
  result: 'Made by a task',
};

export default function TaskFiles({ desktopId, enabled }) {
  const [state, setState] = useState(null);
  const [session, setSession] = useState(null);
  const [problem, setProblem] = useState(null);
  const [busy, setBusy] = useState(false);
  const picker = useRef(null);

  const load = useCallback(async () => {
    if (!enabled) return;
    try {
      const [files, kept] = await Promise.all([
        desktopsApi.files(desktopId),
        desktopsApi.websiteSession(desktopId),
      ]);
      setState(files);
      setSession(kept);
      setProblem(null);
    } catch (failure) {
      setProblem(failure);
    }
  }, [desktopId, enabled]);

  useEffect(() => {
    load();
  }, [load]);

  const act = useCallback(
    async (work) => {
      setBusy(true);
      setProblem(null);
      try {
        await work();
        await load();
      } catch (failure) {
        setProblem(failure);
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  const add = useCallback(
    (event) => {
      const file = event.target.files?.[0];
      // The input is cleared either way: picking the same file twice in a row
      // should work, and it will not fire a change event if the value is still
      // there from last time.
      event.target.value = '';
      if (!file) return;
      const limit = state?.limits?.maxUploadBytes;
      if (limit && file.size > limit) {
        setProblem(new Error(`Files can be up to ${size(limit)}. That one is ${size(file.size)}.`));
        return;
      }
      act(() => desktopsApi.addFile(desktopId, file));
    },
    [act, desktopId, state],
  );

  if (!enabled) return null;

  const limits = state?.limits;
  const files = state?.files || [];
  const unresolved = state?.unresolved || [];

  return (
    <section className="task-files" aria-labelledby="task-files-title">
      <h3 id="task-files-title">Files</h3>

      {unresolved.length > 0 && (
        <div className="task-unresolved" role="alert">
          <p>
            Something was sent to a website and no answer came back. Vela cannot tell whether it
            went through, and will not send it again on its own.
          </p>
          <ul>
            {unresolved.map((entry) => (
              <li key={entry.digest}>
                <span>
                  {entry.method} {entry.url}
                </span>
                <Button
                  size="small"
                  disabled={busy}
                  onClick={() => act(() => desktopsApi.clearUnresolved(desktopId, entry.digest))}
                >
                  I checked
                </Button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="form-actions">
        <input
          ref={picker}
          type="file"
          onChange={add}
          aria-label="Choose a file for this desktop"
        />
        <Button size="small" disabled={busy} onClick={() => picker.current?.click()}>
          Add a file
        </Button>
        {limits && (
          <span className="field-hint">
            Up to {size(limits.maxUploadBytes)} each, {size(limits.maxTaskBytes)} per task.
          </span>
        )}
      </div>

      {files.length > 0 ? (
        <ul className="file-list">
          {files.map((file) => (
            <li key={file.id}>
              <div>
                <a href={desktopsApi.fileUrl(desktopId, file.id)} download={file.name}>
                  {file.name}
                </a>
                <small>
                  {SOURCE_WORDS[file.source] || file.source} · {size(file.bytes)}
                  {file.origin ? ` · from ${file.origin}` : ''}
                </small>
              </div>
              <Button
                size="small"
                variant="ghost"
                disabled={busy}
                onClick={() => act(() => desktopsApi.removeFile(desktopId, file.id))}
              >
                Remove
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="field-hint">
          Nothing yet. A file you add here can be attached to a page by a task; anything a task
          downloads appears here.
        </p>
      )}

      {session && (
        <div className="session-keeping">
          <h4>Website sign-ins</h4>
          {session.allowed ? (
            <p className="field-hint">
              {session.remembered
                ? `Kept for ${session.origins.length || 'no'} site${
                    session.origins.length === 1 ? '' : 's'
                  }: ${session.origins.join(', ') || '—'}.`
                : 'Nothing is being kept yet. Sign in in the window, then keep it.'}
            </p>
          ) : (
            <p className="field-hint">
              This desktop forgets website sign-ins when its browser closes. Change that in its
              settings if you want them kept.
            </p>
          )}
          <div className="form-actions">
            <Button
              size="small"
              disabled={busy || !session.allowed}
              onClick={() => act(() => desktopsApi.keepWebsiteSession(desktopId))}
            >
              Keep them now
            </Button>
            <Button
              size="small"
              variant="danger"
              disabled={busy || !session.remembered}
              onClick={() => act(() => desktopsApi.eraseWebsiteSession(desktopId))}
            >
              Erase
            </Button>
          </div>
        </div>
      )}

      {problem && (
        <p role="alert" className="agent-blocked">
          {problem.message}
        </p>
      )}
    </section>
  );
}
