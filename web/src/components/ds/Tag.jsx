/** A small outlined label: a category, a version, a count. */
export default function Tag({ tone, className = '', children, ...rest }) {
  const classes = ['vela-tag', className].filter(Boolean).join(' ');
  return (
    <span className={classes} data-tone={tone} {...rest}>
      {children}
    </span>
  );
}
