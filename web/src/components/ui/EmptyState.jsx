export default function EmptyState({ title, description, children }) {
  return (
    <div className="state-block">
      <h2>{title}</h2>
      {description && <p>{description}</p>}
      {children}
    </div>
  );
}
