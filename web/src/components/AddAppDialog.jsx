import { useRef, useState } from 'react';
import { FileZip, FolderOpen, Globe } from '@phosphor-icons/react';
import { useApps } from '../store.jsx';
import Button from './ui/Button.jsx';
import Dialog from './ui/Dialog.jsx';

// The supported ways to add an app, in one place. Every option here is backed
// by an existing service: a release archive, a folder on the computer running
// Vela, or a connection to an HTTPS service you already run. Manifest URLs and
// pasted JSON are not supported sources and are deliberately absent.
const SOURCES = [
  {
    key: 'zip',
    icon: FileZip,
    title: 'Release archive',
    hint: 'A .zip built from an app repository, up to 32 MiB',
  },
  {
    key: 'folder',
    icon: FolderOpen,
    title: 'Folder on the server computer',
    hint: 'A path on the machine running Vela, not on this device',
  },
  {
    key: 'connect',
    icon: Globe,
    title: 'Connect a web service',
    hint: 'An HTTPS service you already run, kept on its own address',
  },
];

export default function AddAppDialog({ onClose, onConnect }) {
  const { reviewRelease } = useApps();
  const [source, setSource] = useState('zip');
  const [folder, setFolder] = useState('');
  const [error, setError] = useState('');
  const closeRef = useRef(null);

  const chooseFile = (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 32 * 1024 * 1024) {
      setError('Release archive exceeds 32 MiB.');
      return;
    }
    setError('');
    onClose();
    reviewRelease({ file });
  };

  const submitFolder = (event) => {
    event.preventDefault();
    if (!folder.trim()) return;
    onClose();
    reviewRelease({ folder: folder.trim() });
  };

  return (
    <Dialog
      open
      onClose={onClose}
      closeOnBackdrop
      initialFocusRef={closeRef}
      aria-labelledby="add-app-title"
    >
      <h2 id="add-app-title">Add an app</h2>
      <p className="dialog-note">
        You review what an app asks for before anything is installed or started.
      </p>

      <div className="source-list" role="radiogroup" aria-labelledby="add-app-title">
        {SOURCES.map(({ key, icon: Icon, title, hint }) => (
          <button
            key={key}
            type="button"
            role="radio"
            aria-checked={source === key}
            className={`source-option${source === key ? ' source-option-active' : ''}`}
            onClick={() => setSource(key)}
          >
            <span className="source-option-glyph" aria-hidden="true">
              <Icon size={18} />
            </span>
            <span className="source-option-text">
              <span className="source-option-title">{title}</span>
              <span className="source-option-hint">{hint}</span>
            </span>
          </button>
        ))}
      </div>

      {source === 'zip' && (
        <label className="source-field">
          <span>Release archive</span>
          <input type="file" accept=".zip,application/zip" onChange={chooseFile} />
        </label>
      )}

      {source === 'folder' && (
        <form className="source-field" onSubmit={submitFolder}>
          <label htmlFor="add-app-folder">App folder on the server computer</label>
          <div className="source-field-row">
            <input
              id="add-app-folder"
              className="connection-input"
              value={folder}
              onChange={(event) => setFolder(event.target.value)}
              required
              placeholder="Absolute path to a folder containing app.json"
            />
            <Button type="submit">Review folder</Button>
          </div>
        </form>
      )}

      {source === 'connect' && (
        <div className="source-field">
          <p className="dialog-note">
            Vela saves the address and opens the service in its own frame. It keeps its own login
            and data, and removing the connection never stops or uninstalls it.
          </p>
          <Button
            variant="primary"
            onClick={() => {
              onClose();
              onConnect();
            }}
          >
            Set up a connection
          </Button>
        </div>
      )}

      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}

      <div className="dialog-actions">
        <Button ref={closeRef} onClick={onClose}>
          Cancel
        </Button>
      </div>
    </Dialog>
  );
}
