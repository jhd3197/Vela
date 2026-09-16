// Report the dashboard's own failures to the engine, so a bug that only
// happens on someone else's machine is something they can show you.
//
// What is hooked: this dashboard. App frames are deliberately left alone — an
// app's errors are the app's, and Vela is not the place they get recorded.
// What is sent: the message, the error type, the stack and the route. Never
// the page's content, never a token; the engine is on this computer and the
// report goes nowhere else.
import { api } from './api.js';

// The engine caps reports too. This one keeps a failing render loop from
// sending hundreds of requests before the engine has a chance to refuse them.
const LIMIT_PER_MINUTE = 20;
const WINDOW_MS = 60_000;

let sent = [];
let installed = false;

function allowed() {
  const now = Date.now();
  sent = sent.filter((at) => now - at < WINDOW_MS);
  if (sent.length >= LIMIT_PER_MINUTE) return false;
  sent.push(now);
  return true;
}

/** Send one failure. Reporting must never itself throw. */
export function reportError(error, { type, url } = {}) {
  try {
    if (!allowed()) return;
    const message = String(error?.message || error || 'Unknown error').slice(0, 2000);
    if (!message) return;
    api
      .reportClientError({
        message,
        type: type || error?.name || 'Error',
        stack: error?.stack ? String(error.stack).slice(0, 20000) : undefined,
        url: url || location.pathname + location.search,
      })
      .catch(() => {
        // The engine is unreachable or refusing reports. Nothing to do here:
        // a failed report must not become a second failure.
      });
  } catch {
    // Same reason.
  }
}

/** Hook the two events the browser raises for unhandled failures. */
export function installErrorReporting() {
  if (installed) return;
  installed = true;
  addEventListener('error', (event) => {
    // A failed <img> or <script> also raises this, without an error object.
    if (!event.error) return;
    reportError(event.error);
  });
  addEventListener('unhandledrejection', (event) => {
    reportError(event.reason, { type: 'UnhandledRejection' });
  });
}

// Reset between tests, and after a suite that deliberately floods the cap.
export function resetErrorReporting() {
  sent = [];
}
