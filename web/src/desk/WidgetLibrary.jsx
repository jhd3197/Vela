// Add a widget.
//
// Adapted from ServerKit's `grid/WidgetLibrary.jsx` (MIT, same owner): the
// grouped, searchable catalogue of placeable types is the same idea, inside
// Vela's `Drawer` and without ServerKit's per-metric configuration step. A
// widget is added where it fits and the desk goes straight into Arrange mode,
// so the next thing the user does is put it where they want it.
import { useMemo, useState } from 'react';
import { MagnifyingGlass, SquaresFour, X } from '@phosphor-icons/react';
import Drawer from '../components/ui/Drawer.jsx';

function sizeLabel(type) {
  return `${type.w} x ${type.h}`;
}

export default function WidgetLibrary({ types, cols, onAdd, onClose }) {
  const [query, setQuery] = useState('');

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matched = types.filter((type) =>
      needle ? `${type.name} ${type.desc} ${type.cat}`.toLowerCase().includes(needle) : true,
    );
    const byCategory = new Map();
    for (const type of matched) {
      const key = type.cat || 'Vela';
      if (!byCategory.has(key)) byCategory.set(key, []);
      byCategory.get(key).push(type);
    }
    // Vela's own widgets first, then each app in name order, so the list does
    // not reshuffle as apps come and go.
    return [...byCategory.entries()].sort(([a], [b]) =>
      a === 'Vela' ? -1 : b === 'Vela' ? 1 : a.localeCompare(b),
    );
  }, [types, query]);

  return (
    <Drawer open onClose={onClose} aria-label="Add a widget" panelClassName="desk-library">
      <div className="drawer-header">
        <div className="drawer-title-row">
          <h2 className="drawer-name">Add a widget</h2>
        </div>
        <button className="drawer-close" onClick={onClose} aria-label="Close the widget list">
          <X size={20} />
        </button>
      </div>
      <div className="desk-library-search">
        <MagnifyingGlass size={15} aria-hidden="true" />
        <input
          type="search"
          aria-label="Find a widget"
          placeholder="Find a widget…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      <div className="drawer-body">
        {groups.length === 0 && (
          <p className="desk-empty" role="status">
            No widgets match “{query.trim()}”.
          </p>
        )}
        {groups.map(([category, entries]) => (
          <section key={category} className="desk-library-group">
            <h3 className="section-head">{category}</h3>
            <div className="desk-library-grid">
              {entries.map((type) => {
                const Icon = type.icon || SquaresFour;
                return (
                  <button
                    key={type.id}
                    type="button"
                    className="desk-library-card"
                    onClick={() => onAdd(type)}
                  >
                    <span className="desk-library-thumb" aria-hidden="true">
                      <Icon size={18} />
                    </span>
                    <span className="desk-library-text">
                      <span className="desk-library-name">{type.name}</span>
                      <span className="desk-library-desc">{type.desc}</span>
                    </span>
                    <span className="desk-library-size" aria-hidden="true">
                      {sizeLabel({ ...type, w: Math.min(type.w, cols) })}
                    </span>
                  </button>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </Drawer>
  );
}
