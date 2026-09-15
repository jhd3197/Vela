import { tileColor, tileTones } from '../appTint.js';
import { appArtwork } from './appArtwork.jsx';

// The app tile. Colour comes from the app's declared identity and the
// silhouette from its own artwork, so two apps are told apart by shape first
// and hue second — both still legible at rail size. The artwork is decorative,
// so the tile stays hidden from screen readers and every control that uses it
// keeps its own accessible name.
export default function AppIcon({ app, size = 44, plain }) {
  const art = appArtwork(app);
  const Glyph = app?.glyph || null;
  const mark = Math.round(size * art.scale);
  // An unreadable colour falls back to the neutral tile rather than a tile
  // with no ground at all.
  const tones = plain ? null : tileTones(tileColor(app));
  const isPlain = Boolean(plain) || !tones;
  const style = {
    width: size,
    height: size,
    borderRadius: Math.max(8, size * 0.29),
  };
  if (tones) {
    style['--tile-mark'] = tones.mark;
    style['--tile-wash'] = tones.wash;
    style['--tile-line'] = tones.line;
    style['--tile-mark-dark'] = tones.markDark;
    style['--tile-wash-dark'] = tones.washDark;
    style['--tile-line-dark'] = tones.lineDark;
  }

  return (
    <span className={`appicon${isPlain ? ' appicon-plain' : ''}`} style={style} aria-hidden="true">
      {Glyph ? (
        <Glyph size={mark} weight="regular" />
      ) : (
        <svg
          viewBox="0 0 96 96"
          width={mark}
          height={mark}
          focusable="false"
          className={art.monogram ? 'appicon-monogram' : undefined}
        >
          {art.draw}
        </svg>
      )}
    </span>
  );
}
