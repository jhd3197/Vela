import Card from '../../components/ds/Card.jsx';

/**
 * The prototype's widget recipe: a tinted icon, a 14px/500 heading, an optional
 * trailing note, then the body.
 *
 * This is the widget's own content and not the frame's chrome. In view mode
 * `WidgetFrame` draws no header at all -- the board is a set of cards, not a
 * set of title bars -- so a widget that wants to say what it is says it here,
 * and it scrolls, wraps and is read as part of the widget.
 *
 * The icon and the name come from the widget's own type, so a widget does not
 * repeat what the Widget Library already knows about it. Only the tone and the
 * trailing note are the widget's to choose, because only the widget knows
 * whether it is currently fine.
 */
export default function WidgetCard({ type, title, tone = 'accent', meta, footer, children }) {
  const Icon = type?.icon;
  return (
    <Card
      plain
      tone={tone}
      icon={Icon ? <Icon size={15} weight="regular" aria-hidden="true" /> : null}
      title={title ?? type?.name}
      meta={meta}
      footer={footer}
    >
      {children}
    </Card>
  );
}
