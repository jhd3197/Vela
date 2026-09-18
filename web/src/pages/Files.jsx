// Files: the folders you told Vela it may show you.
//
// Not a file manager for the whole computer. The server only serves what is
// inside a configured share, and this page can only ask for what the server
// will answer — a share id and a path inside it. There is no field here that
// takes a path of your choosing, because there is no route that would accept
// one.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowClockwise,
  CaretRight,
  DownloadSimple,
  File as FileIcon,
  FileAudio,
  FilePdf,
  FileText,
  FileVideo,
  FileZip,
  Folder,
  FolderPlus,
  Image as ImageIcon,
  ListBullets,
  PencilSimple,
  SquaresFour,
  Trash,
  UploadSimple,
} from '@phosphor-icons/react';
import { api, formatBytes, relTime } from '../api.js';
import { useResource } from '../hooks/useResource.js';
import { useApps } from '../store.jsx';
import useMediaQuery from '../hooks/useMediaQuery.js';
import { PHONE } from '../breakpoints.js';
import WorkspacePage from '../components/WorkspacePage.jsx';
import Button from '../components/ui/Button.jsx';
import Dialog from '../components/ui/Dialog.jsx';

// One icon per kind the server reports, so a listing is read by shape before
// it is read by name.
const ICONS = {
  folder: Folder,
  image: ImageIcon,
  video: FileVideo,
  audio: FileAudio,
  pdf: FilePdf,
  text: FileText,
  archive: FileZip,
  file: FileIcon,
};

// What a browser will show without downloading. SVG is missing on purpose: it
// is an image that can carry script, and the server refuses to show it inline.
const PREVIEWABLE = new Set(['image', 'video', 'audio', 'pdf', 'text']);

function crumbsOf(path) {
  const parts = path ? path.split('/').filter(Boolean) : [];
  return parts.map((name, index) => ({ name, path: parts.slice(0, index + 1).join('/') }));
}

export default function Files() {
  const { pushToast } = useApps();
  const phone = useMediaQuery(PHONE);
  const [shareId, setShareId] = useState('');
  const [path, setPath] = useState('');
  const [view, setView] = useState('list');
  const [selected, setSelected] = useState(() => new Set());
  const [renaming, setRenaming] = useState(null);
  const [newFolder, setNewFolder] = useState(false);
  const [draftName, setDraftName] = useState('');
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [dropping, setDropping] = useState(false);
  const fileInput = useRef(null);
  const listRef = useRef(null);

  const loadShares = useCallback((options) => api.fileShares(options), []);
  const { data: sharesData, error: sharesError } = useResource(loadShares);
  const shares = useMemo(() => sharesData?.shares || [], [sharesData]);

  // The first reachable share is the one to open on, so a page with a broken
  // drive configured does not open on an error.
  useEffect(() => {
    if (shareId || !shares.length) return;
    setShareId((shares.find((entry) => entry.reachable) || shares[0]).id);
  }, [shares, shareId]);

  const loadListing = useCallback(
    (options) => (shareId ? api.listFiles(shareId, path, options) : Promise.resolve(null)),
    [shareId, path],
  );
  const { data: listing, error, loading, refresh } = useResource(loadListing);
  const entries = useMemo(() => listing?.entries || [], [listing]);
  const share = shares.find((entry) => entry.id === shareId);
  const writable = listing?.share?.writable ?? share?.writable ?? false;

  // Moving folder is a fresh selection; keeping it would let an action land on
  // something that is no longer on screen.
  useEffect(() => {
    setSelected(new Set());
  }, [shareId, path]);

  const act = async (work, done) => {
    setBusy(true);
    try {
      await work();
      refresh();
      if (done) pushToast(done, 'success');
    } catch (failed) {
      pushToast(failed.message || 'That did not work.', 'error');
    } finally {
      setBusy(false);
    }
  };

  // The engine authenticates by header, so a file is fetched with the session
  // and handed to the browser as a blob rather than linked to directly.
  const download = async (entry) => {
    try {
      const url = await api.fileBlobUrl(shareId, entry.path);
      const link = document.createElement('a');
      link.href = url;
      link.download = entry.name;
      document.body.append(link);
      link.click();
      link.remove();
      // Revoked on the next tick so the download has taken the reference.
      setTimeout(() => URL.revokeObjectURL(url), 30000);
    } catch (failed) {
      pushToast(failed.message || 'Could not download that.', 'error');
    }
  };

  const open = (entry) => {
    if (entry.kind === 'folder') {
      setPath(entry.path);
      return;
    }
    if (PREVIEWABLE.has(entry.kind)) setPreview(entry);
    else download(entry);
  };

  const toggle = (entry, event) => {
    setSelected((current) => {
      const next = new Set(event?.ctrlKey || event?.metaKey ? current : []);
      if (current.has(entry.path) && (event?.ctrlKey || event?.metaKey)) next.delete(entry.path);
      else next.add(entry.path);
      return next;
    });
  };

  const upload = async (list) => {
    const chosen = [...list];
    if (!chosen.length) return;
    await act(
      async () => {
        for (const file of chosen) await api.uploadFile(shareId, path, file);
      },
      `Added ${chosen.length === 1 ? chosen[0].name : `${chosen.length} files`}.`,
    );
  };

  const removeSelected = () =>
    act(
      async () => {
        for (const target of selected) await api.deleteFile(shareId, target);
      },
      `Moved ${selected.size === 1 ? 'it' : `${selected.size} items`} to the trash.`,
    );

  const onKeyDown = (event) => {
    const rows = [...(listRef.current?.querySelectorAll('.files-row') || [])];
    const index = rows.indexOf(event.currentTarget);
    if (index < 0) return;
    const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
    if (step) {
      event.preventDefault();
      rows[index + step]?.focus();
    }
  };

  const crumbs = crumbsOf(path);

  return (
    <WorkspacePage title="Files" subtitle="The folders you shared with Vela" scroll>
      <div className="page-inner files">
        {sharesError ? (
          <p className="inline-error" role="alert">
            Could not read your shares.
          </p>
        ) : null}

        <div className="files-shares" role="tablist" aria-label="Shares">
          {shares.map((entry) => (
            <button
              key={entry.id}
              type="button"
              role="tab"
              aria-selected={entry.id === shareId}
              className={`files-share${entry.id === shareId ? ' is-active' : ''}`}
              disabled={!entry.reachable}
              title={entry.reachable ? entry.path : `${entry.path} is not available right now`}
              onClick={() => {
                setShareId(entry.id);
                setPath('');
              }}
            >
              <Folder size={16} aria-hidden="true" />
              {entry.label}
              {!entry.reachable && <span className="files-share-off">unavailable</span>}
            </button>
          ))}
          {!shares.length && !sharesError ? (
            <p className="panel-note">No shares yet. Add one in Settings → Files.</p>
          ) : null}
        </div>

        <div className="files-bar">
          <nav className="files-crumbs" aria-label="Folder">
            <button type="button" className="linklike" onClick={() => setPath('')}>
              {share?.label || 'Share'}
            </button>
            {crumbs.map((crumb) => (
              <span key={crumb.path}>
                <CaretRight size={12} aria-hidden="true" />
                <button
                  type="button"
                  className="linklike"
                  aria-current={crumb.path === path ? 'page' : undefined}
                  onClick={() => setPath(crumb.path)}
                >
                  {crumb.name}
                </button>
              </span>
            ))}
          </nav>

          <div className="files-actions">
            {selected.size > 0 && writable && (
              <>
                {selected.size === 1 && (
                  <Button
                    size="small"
                    disabled={busy}
                    onClick={() => {
                      const only = [...selected][0];
                      setDraftName(only.split('/').pop());
                      setRenaming(only);
                    }}
                  >
                    <PencilSimple size={14} aria-hidden="true" />
                    Rename
                  </Button>
                )}
                <Button size="small" variant="ghost" disabled={busy} onClick={removeSelected}>
                  <Trash size={14} aria-hidden="true" />
                  Delete
                </Button>
              </>
            )}
            {writable && (
              <>
                <Button
                  size="small"
                  disabled={busy}
                  onClick={() => {
                    setDraftName('');
                    setNewFolder(true);
                  }}
                >
                  <FolderPlus size={14} aria-hidden="true" />
                  New folder
                </Button>
                <Button size="small" disabled={busy} onClick={() => fileInput.current?.click()}>
                  <UploadSimple size={14} aria-hidden="true" />
                  Upload
                </Button>
              </>
            )}
            <Button size="small" variant="ghost" disabled={busy} onClick={refresh}>
              <ArrowClockwise size={14} aria-hidden="true" />
              Refresh
            </Button>
            <button
              type="button"
              className="icon-btn"
              aria-label={view === 'list' ? 'Show as a grid' : 'Show as a list'}
              onClick={() => setView(view === 'list' ? 'grid' : 'list')}
            >
              {view === 'list' ? <SquaresFour size={16} /> : <ListBullets size={16} />}
            </button>
          </div>
        </div>

        <input
          ref={fileInput}
          type="file"
          multiple
          className="sr-only"
          aria-label="Choose files to upload"
          onChange={(event) => {
            upload(event.target.files);
            event.target.value = '';
          }}
        />

        <div
          className={`files-list files-list-${view}${dropping ? ' is-dropping' : ''}${
            phone ? ' files-list-phone' : ''
          }`}
          ref={listRef}
          onDragOver={(event) => {
            if (!writable) return;
            event.preventDefault();
            setDropping(true);
          }}
          onDragLeave={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) setDropping(false);
          }}
          onDrop={(event) => {
            if (!writable) return;
            event.preventDefault();
            setDropping(false);
            upload(event.dataTransfer.files);
          }}
        >
          {error ? (
            <p className="inline-error" role="alert">
              {error.message || 'Could not read that folder.'}
            </p>
          ) : loading && !listing ? (
            <p className="vela-empty" role="status">
              Loading…
            </p>
          ) : entries.length === 0 ? (
            <p className="vela-empty" role="status">
              {writable ? 'Nothing here yet. Drop a file to add one.' : 'Nothing here.'}
            </p>
          ) : (
            entries.map((entry) => {
              const Icon = ICONS[entry.kind] || FileIcon;
              return (
                <button
                  key={entry.path}
                  type="button"
                  className={`files-row${selected.has(entry.path) ? ' is-selected' : ''}`}
                  aria-pressed={selected.has(entry.path)}
                  onKeyDown={onKeyDown}
                  onDoubleClick={() => open(entry)}
                  onClick={(event) => {
                    if (event.detail === 0) open(entry);
                    else toggle(entry, event);
                  }}
                >
                  <Icon size={view === 'grid' ? 28 : 18} aria-hidden="true" />
                  <span className="files-name">{entry.name}</span>
                  <span className="files-meta">
                    {entry.size === null ? '' : formatBytes(entry.size)}
                  </span>
                  <span className="files-meta files-when">{relTime(entry.modified)}</span>
                </button>
              );
            })
          )}
          {listing?.truncated ? (
            <p className="panel-note">
              This folder has more than Vela shows at once. Open it on this computer to see the
              rest.
            </p>
          ) : null}
        </div>
      </div>

      <Dialog
        open={Boolean(preview)}
        aria-label={preview ? `Preview of ${preview.name}` : 'Preview'}
        onClose={() => setPreview(null)}
      >
        <h2>{preview?.name}</h2>
        {preview ? <FilePreview share={shareId} entry={preview} /> : null}
        <div className="form-actions">
          <Button onClick={() => download(preview)}>
            <DownloadSimple size={15} aria-hidden="true" />
            Download
          </Button>
          <Button variant="ghost" onClick={() => setPreview(null)}>
            Close
          </Button>
        </div>
      </Dialog>

      <Dialog
        open={newFolder || Boolean(renaming)}
        aria-label={renaming ? 'Rename' : 'New folder'}
        onClose={() => {
          setNewFolder(false);
          setRenaming(null);
        }}
      >
        <h2>{renaming ? 'Rename' : 'New folder'}</h2>
        <label className="field-label" htmlFor="files-name">
          Name
          <input
            id="files-name"
            className="field"
            value={draftName}
            autoFocus
            onChange={(event) => setDraftName(event.target.value)}
          />
        </label>
        <div className="form-actions">
          <Button
            disabled={busy || !draftName.trim()}
            onClick={async () => {
              const name = draftName.trim();
              const target = renaming;
              setNewFolder(false);
              setRenaming(null);
              await act(
                () =>
                  target
                    ? api.renameFile(shareId, target, name)
                    : api.createFolder(shareId, path, name),
                target ? 'Renamed.' : 'Folder added.',
              );
            }}
          >
            {renaming ? 'Rename' : 'Create'}
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              setNewFolder(false);
              setRenaming(null);
            }}
          >
            Cancel
          </Button>
        </div>
      </Dialog>
    </WorkspacePage>
  );
}

// The preview itself. Everything is fetched with the hub session and shown from
// a blob, because the engine authenticates by header and an `<img src>` cannot
// carry one. Text is shown as text rather than rendered, so a shared HTML file
// is read, never run.
function FilePreview({ share, entry }) {
  const [url, setUrl] = useState(null);
  const [text, setText] = useState(null);
  const [failed, setFailed] = useState('');

  useEffect(() => {
    let cancelled = false;
    let made = null;
    setUrl(null);
    setText(null);
    setFailed('');
    api
      .fileBlobUrl(share, entry.path, { inline: true })
      .then(async (blobUrl) => {
        if (cancelled) {
          URL.revokeObjectURL(blobUrl);
          return;
        }
        made = blobUrl;
        setUrl(blobUrl);
        if (entry.kind === 'text') {
          const body = await (await fetch(blobUrl)).text();
          if (!cancelled) setText(body.slice(0, 200000));
        }
      })
      .catch((error) => {
        if (!cancelled) setFailed(error.message || 'Could not read that file.');
      });
    return () => {
      cancelled = true;
      if (made) URL.revokeObjectURL(made);
    };
  }, [share, entry.path, entry.kind]);

  if (failed)
    return (
      <p className="inline-error" role="alert">
        {failed}
      </p>
    );
  if (entry.kind === 'text')
    return (
      <pre className="files-preview files-preview-text">
        {text === null ? 'Loading…' : text || 'This file is empty.'}
      </pre>
    );
  if (!url)
    return (
      <p className="vela-empty" role="status">
        Loading…
      </p>
    );
  if (entry.kind === 'image') return <img className="files-preview" src={url} alt={entry.name} />;
  if (entry.kind === 'video')
    return <video className="files-preview" src={url} controls aria-label={entry.name} />;
  if (entry.kind === 'audio') return <audio src={url} controls aria-label={entry.name} />;
  if (entry.kind === 'pdf')
    return <iframe className="files-preview" src={url} title={entry.name} />;
  return <p className="panel-note">Vela cannot show this kind of file. Download it instead.</p>;
}
