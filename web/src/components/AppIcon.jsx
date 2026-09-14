import { appColor } from '../api.js';
import {
  ForkKnife,
  HardDrives,
  Heartbeat,
  NotePencil,
  Sailboat,
  ShieldCheck,
  SquaresFour,
  Wallet,
} from '@phosphor-icons/react';

// Gradient icon tile with a white Phosphor glyph, per the Nocturne
// prototypes. Apps with a manifest `color` get a colored gradient derived
// from it; apps without one get the plain utility tile. The glyph is chosen
// per app id, then per category, with a generic grid as fallback.
const ID_GLYPHS = {
  notes: NotePencil,
  meals: ForkKnife,
  health: Heartbeat,
  finance: Wallet,
  'system-info': HardDrives,
  'hello-vela': Sailboat,
  'vela-core': ShieldCheck,
};

const CATEGORY_GLYPHS = {
  productivity: NotePencil,
  wellness: Heartbeat,
  lifestyle: ForkKnife,
  finance: Wallet,
  utilities: HardDrives,
};

export default function AppIcon({ app, size = 44, plain }) {
  const color = app?.color ? appColor(app) : null;
  const Glyph = app?.glyph || ID_GLYPHS[app?.id] || CATEGORY_GLYPHS[app?.category] || SquaresFour;
  const isPlain = plain ?? !color;
  const style = {
    width: size,
    height: size,
    borderRadius: Math.max(8, size * 0.29),
  };
  if (color) style['--tile-color'] = color;

  return (
    <span className={`appicon${isPlain ? ' appicon-plain' : ''}`} style={style} aria-hidden="true">
      <Glyph size={size * 0.52} weight={isPlain ? 'regular' : 'fill'} />
    </span>
  );
}
