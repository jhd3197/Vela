/**
 * The card every widget is drawn on: icon, title, optional trailing note, body,
 * optional footer.
 *
 * A thin function over `.vela-card`. It fetches nothing and knows nothing about
 * the desk -- an app's published widget, a Marketplace entry and a Settings
 * section all render this, which is what makes them look like one system.
 *
 * `plain` is for a card sitting inside something that has already painted the
 * surface, such as a desk frame over the wallpaper: a second fill inside that
 * shows as a lighter rectangle.
 */
export default function Card({
  icon = null,
  title = null,
  meta = null,
  tone,
  plain = false,
  footer = null,
  className = '',
  children,
  ...rest
}) {
  const classes = ['vela-card', className].filter(Boolean).join(' ');
  return (
    <div className={classes} data-plain={plain ? '' : undefined} {...rest}>
      {(icon || title || meta) && (
        <div className="vela-card-head">
          {icon ? (
            <span className="vela-card-icon" data-tone={tone} aria-hidden="true">
              {icon}
            </span>
          ) : null}
          {title ? <h3 className="vela-card-title">{title}</h3> : null}
          {meta ? <span className="vela-card-meta">{meta}</span> : null}
        </div>
      )}
      <div className="vela-card-body">{children}</div>
      {footer ? <div className="vela-card-foot">{footer}</div> : null}
    </div>
  );
}
