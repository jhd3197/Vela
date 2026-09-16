import { useCallback, useState } from 'react';
import { api, formatBytes, relTime } from '../api.js';
import { useResource } from '../hooks/useResource.js';
import { useApps } from '../store.jsx';
import Button from './ui/Button.jsx';

// "Create support bundle": one file describing this server, built here and
// kept here. The copy says plainly that Vela sends nothing — because it does
// not, and because a button that gathers logs should say where they go.
export default function SupportBundlePanel() {
  const { pushToast } = useApps();
  const [building, setBuilding] = useState(false);
  const [downloading, setDownloading] = useState(null);
  const load = useCallback((options) => api.getSupportBundles(options), []);
  const { data, refresh } = useResource(load);
  const bundles = data?.bundles || [];

  const build = async () => {
    setBuilding(true);
    try {
      const made = await api.createSupportBundle();
      pushToast(`${made.name} created (${formatBytes(made.size)}).`, 'success');
      await refresh();
    } catch (error) {
      pushToast(error.message || 'Could not build a support bundle.');
    } finally {
      setBuilding(false);
    }
  };

  const download = async (name) => {
    setDownloading(name);
    try {
      const blob = await api.downloadSupportBundle(name);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      pushToast(error.message || `Could not download ${name}.`);
    } finally {
      setDownloading(null);
    }
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Support bundle</h2>
        <Button size="small" pending={building} onClick={build}>
          Create support bundle
        </Button>
      </div>
      <p className="panel-note">
        A single file describing how this server is set up and what has gone wrong on it: your
        settings with every password and token replaced, the last health check, which apps are
        installed, and the tail of each log. It never includes what your apps saved, your chats or
        your wallpapers. Vela writes it to this computer and sends it nowhere — sharing it is your
        decision. Bundles older than seven days are removed.
      </p>
      {bundles.length > 0 && (
        <ul className="mini-list">
          {bundles.map((bundle) => (
            <li key={bundle.name}>
              <span className="mini-list-name mono">{bundle.name}</span>
              <span className="panel-note">
                {formatBytes(bundle.size)} · {relTime(bundle.created_at)}
              </span>
              <Button
                size="small"
                pending={downloading === bundle.name}
                onClick={() => download(bundle.name)}
              >
                Download
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
