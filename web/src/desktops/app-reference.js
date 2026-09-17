// What a dragged app card actually carries.
//
// Two rules, and the second one is the reason this is a module instead of three
// `getData` calls in three components.
//
// **A drop is a reference, never a payload.** What travels is an app's
// identity: its id, its name, whether it is installed and which installation
// that is. Never markup, never a token, never anything an app had in it. A
// split screen does not grant cross-app access, and a drag between two panes is
// not a channel between two apps.
//
// **A drop is not permission.** Attaching an app to a task names it for
// discussion. It does not install anything, does not submit the instruction,
// does not invoke an action and does not widen what a desktop may use. The
// person still presses the button that starts the work.
//
// The plain id type is kept beside the typed one because the desk board has
// accepted it since before any of this existed, and a surface that only
// understood the new shape would silently stop accepting the old drags.

/** The typed reference. Anything that reads this validates it. */
export const APP_REFERENCE = 'application/x-vela-app-reference';

/** The older, plainer type: an app id and nothing else. Still accepted. */
export const APP_ID = 'application/x-vela-app';

/** Where the drag came from. Recorded because it changes what a drop may offer. */
export const SOURCES = ['launchpad', 'library', 'rail', 'desk'];

/**
 * Put an app on a drag, in both shapes.
 *
 * `text/plain` is the name, which is what a drop onto a text field pastes —
 * chosen deliberately over the id, because a person reading the result wants
 * the app's name and not its slug.
 */
export function writeAppReference(dataTransfer, app, source = 'launchpad') {
  if (!dataTransfer || !app?.id) return;
  const reference = {
    kind: 'app',
    id: String(app.id),
    name: String(app.name || app.id),
    installed: Boolean(app.installed),
    installationId: app.installationId ? String(app.installationId) : null,
    source: SOURCES.includes(source) ? source : 'launchpad',
  };
  dataTransfer.setData(APP_REFERENCE, JSON.stringify(reference));
  dataTransfer.setData(APP_ID, reference.id);
  dataTransfer.setData('text/plain', reference.name);
  dataTransfer.effectAllowed = 'copy';
}

/** Whether a drag is carrying an app at all, without reading it. */
export function carriesApp(dataTransfer) {
  const types = Array.from(dataTransfer?.types || []);
  return types.includes(APP_REFERENCE) || types.includes(APP_ID);
}

/**
 * The app a drop is carrying, checked rather than trusted.
 *
 * Returns null for anything that is not one. A malformed payload is not a
 * reason to guess: the thing being decided is which app a person meant, and
 * guessing at that is how the wrong one ends up attached to a task.
 */
export function readAppReference(dataTransfer) {
  if (!dataTransfer) return null;
  const raw = dataTransfer.getData(APP_REFERENCE);
  if (raw) {
    try {
      const value = JSON.parse(raw);
      if (value?.kind === 'app' && typeof value.id === 'string' && value.id) {
        return {
          kind: 'app',
          id: value.id,
          name: typeof value.name === 'string' && value.name ? value.name : value.id,
          installed: Boolean(value.installed),
          installationId: typeof value.installationId === 'string' ? value.installationId : null,
          source: SOURCES.includes(value.source) ? value.source : 'launchpad',
        };
      }
    } catch {
      // A reference Vela cannot read is a reference it does not act on.
    }
  }
  const id = dataTransfer.getData(APP_ID);
  if (!id) return null;
  return { kind: 'app', id, name: id, installed: false, installationId: null, source: 'launchpad' };
}
