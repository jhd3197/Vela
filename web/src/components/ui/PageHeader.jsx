export default function PageHeader({ title, description, actions }) {
  const heading = (
    <>
      <h1 className="page-title">{title}</h1>
      {description && <p className="page-sub">{description}</p>}
    </>
  );
  return actions ? (
    <header className="page-head-row">
      <div>{heading}</div>
      {actions}
    </header>
  ) : (
    <header>{heading}</header>
  );
}
