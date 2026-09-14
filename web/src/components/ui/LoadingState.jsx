export default function LoadingState({ children = 'Loading…' }) {
  return <div className="state-block" role="status">
    <div className="spinner" aria-hidden="true" />
    <p>{children}</p>
  </div>;
}
