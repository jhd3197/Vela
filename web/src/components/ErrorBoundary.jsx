import { Component } from 'react';
import { reportError } from '../errors.js';

// A render that throws takes its whole tree down, leaving a blank page and no
// explanation. This catches it, records it so it can be looked at later, and
// offers the one thing that reliably helps: reload.
//
// It wraps the routes, not the shell, so the rail stays usable and the person
// can go somewhere else instead.
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    reportError(error, { type: error?.name || 'RenderError' });
    // The component stack says which part of the page failed, which the error
    // alone does not.
    if (info?.componentStack) {
      reportError(
        {
          message: `Component stack for: ${error?.message || 'render error'}`,
          stack: info.componentStack,
        },
        { type: 'ComponentStack' },
      );
    }
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="page-inner">
        <section className="panel">
          <div className="panel-head">
            <h2>This page stopped working</h2>
          </div>
          <p className="panel-note">
            Something in the dashboard failed while drawing this page. Vela recorded what happened —
            you can read it later in System › Errors. Nothing was sent anywhere.
          </p>
          <p className="panel-note mono">{String(this.state.error?.message || this.state.error)}</p>
          <div className="actions">
            <button type="button" className="btn btn-primary" onClick={() => location.reload()}>
              Reload
            </button>
            <button type="button" className="btn" onClick={() => this.setState({ error: null })}>
              Try again
            </button>
          </div>
        </section>
      </div>
    );
  }
}
