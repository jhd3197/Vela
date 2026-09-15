export default function StatusBadge({ app }) {
  if (app.kind === 'connected-web') return <span className="badge badge-installed">Web app</span>;
  if (!app.supported) {
    return <span className="badge badge-unsupported">Unsupported</span>;
  }
  if (app.running) {
    return (
      <span className="badge badge-running">
        <span className="pulse-dot" />
        Running
      </span>
    );
  }
  if (app.installed) {
    return <span className="badge badge-installed">Installed</span>;
  }
  return <span className="badge badge-available">Available</span>;
}
