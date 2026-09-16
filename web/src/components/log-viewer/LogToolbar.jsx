import { MagnifyingGlass } from '@phosphor-icons/react';
import Button from '../ui/Button.jsx';
import { LINE_CHOICES } from './logHelpers.js';

// Search, how much to show, auto-refresh, Download and Clear. Adapted from
// ServerKit's `log-viewer/LogToolbar.jsx` (MIT, same owner) with Radix's
// Input/Button/Switch replaced by Vela's own controls.
export default function LogToolbar({
  searchRef,
  pattern,
  onPattern,
  lines,
  onLines,
  live,
  onLive,
  onRefresh,
  onDownload,
  onClear,
  downloading,
  clearing,
  disabled,
}) {
  return (
    <div className="logs-toolbar">
      <div className="searchbox searchbox-inline logs-search">
        <MagnifyingGlass className="searchbox-icon" size={15} aria-hidden="true" />
        <input
          ref={searchRef}
          type="search"
          value={pattern}
          disabled={disabled}
          placeholder="Search this log"
          aria-label="Search this log"
          onChange={(event) => onPattern(event.target.value)}
        />
      </div>

      <label className="logs-lines-choice">
        <span className="logs-toolbar-label">Lines</span>
        <select
          value={lines}
          disabled={disabled}
          aria-label="How many lines to show"
          onChange={(event) => onLines(Number(event.target.value))}
        >
          {LINE_CHOICES.map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </select>
      </label>

      <span className="logs-live">
        <button
          className={`switch${live ? ' switch-on' : ''}`}
          role="switch"
          aria-checked={live}
          disabled={disabled}
          aria-label="Refresh this log automatically"
          onClick={() => onLive(!live)}
        />
        <span className="logs-toolbar-label">Auto-refresh</span>
      </span>

      <div className="logs-toolbar-actions">
        <Button size="small" variant="ghost" disabled={disabled} onClick={onRefresh}>
          Refresh
        </Button>
        <Button
          size="small"
          variant="ghost"
          disabled={disabled}
          pending={downloading}
          onClick={onDownload}
        >
          Download
        </Button>
        <Button
          size="small"
          variant="ghost"
          disabled={disabled}
          pending={clearing}
          onClick={onClear}
        >
          Clear
        </Button>
      </div>
    </div>
  );
}
