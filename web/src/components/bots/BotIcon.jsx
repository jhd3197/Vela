import {
  ChatCircle,
  Code,
  Compass,
  Flask,
  Graph,
  Lightbulb,
  MagnifyingGlass,
  Megaphone,
  Notebook,
  PenNib,
  ShieldCheck,
  Sparkle,
} from '@phosphor-icons/react';

// The closed icon set the server validates against. A profile can only ever
// name one of these, so an unknown value falls back rather than rendering
// nothing.
export const BOT_ICONS = {
  sparkle: Sparkle,
  'pen-nib': PenNib,
  compass: Compass,
  'magnifying-glass': MagnifyingGlass,
  code: Code,
  'chat-circle': ChatCircle,
  lightbulb: Lightbulb,
  notebook: Notebook,
  flask: Flask,
  megaphone: Megaphone,
  'shield-check': ShieldCheck,
  graph: Graph,
};

export const BOT_COLORS = ['indigo', 'violet', 'teal', 'amber', 'rose', 'slate', 'emerald', 'sky'];

export default function BotIcon({ bot, size = 16, className = '' }) {
  const Glyph = BOT_ICONS[bot?.icon] ?? Sparkle;
  return (
    <span
      className={`bot-icon bot-color-${bot?.color ?? 'indigo'} ${className}`.trim()}
      aria-hidden="true"
    >
      <Glyph size={size} />
    </span>
  );
}
