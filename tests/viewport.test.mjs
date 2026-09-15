import test from 'node:test';
import assert from 'node:assert/strict';
import {
  createViewportStore,
  describeViewport,
  hasTextEntryFocus,
  intersectRect,
  occlusionOf,
  readGeometry,
  viewportChanged,
  viewportVariables,
  visibleRect,
  KEYBOARD_MIN_PX,
} from '../web/src/viewport.js';

// A stand-in for the pieces of a window the service reads. No DOM, no browser:
// these checks are about the geometry model and the listener contract.
function fakeWindow({ innerWidth = 390, innerHeight = 844, visual = {}, activeElement = null } = {}) {
  const listeners = new Map();
  const target = (name) => {
    const own = new Map();
    return {
      name,
      own,
      addEventListener(type, handler) {
        if (!own.has(type)) own.set(type, new Set());
        own.get(type).add(handler);
        listeners.set(`${name}:${type}`, own.get(type));
      },
      removeEventListener(type, handler) {
        own.get(type)?.delete(handler);
      },
    };
  };
  const visualViewport = visual && {
    ...target('visual'),
    offsetLeft: 0,
    offsetTop: 0,
    width: innerWidth,
    height: innerHeight,
    scale: 1,
    ...visual,
  };
  const frames = [];
  const win = {
    ...target('window'),
    innerWidth,
    innerHeight,
    visualViewport: visual === null ? undefined : visualViewport,
    requestAnimationFrame(run) {
      frames.push(run);
      return frames.length;
    },
    cancelAnimationFrame(id) {
      frames[id - 1] = null;
    },
  };
  const style = new Map();
  const doc = {
    ...target('document'),
    activeElement,
    documentElement: {
      style: {
        setProperty: (name, value) => style.set(name, value),
        getPropertyValue: (name) => style.get(name) ?? '',
      },
    },
  };
  return {
    window: win,
    document: doc,
    style,
    // How many handlers are attached, across every target.
    get bound() {
      return [...listeners.values()].reduce((total, set) => total + set.size, 0);
    },
    emit(key) {
      for (const handler of listeners.get(key) ?? []) handler();
    },
    flush() {
      const pending = frames.splice(0, frames.length);
      for (const run of pending) run?.();
      return pending.filter(Boolean).length;
    },
    get queued() {
      return frames.filter(Boolean).length;
    },
  };
}

const field = (type = 'text') => ({ tagName: 'INPUT', type });

test('the visible rectangle is intersected, never assumed', () => {
  const visible = visibleRect({ left: 0, top: 120, width: 390, height: 400 });
  // A frame that starts above the visible rectangle and runs past its bottom.
  const frame = { left: 0, top: 52, right: 390, bottom: 844 };
  assert.deepEqual(intersectRect(frame, visible), {
    left: 0,
    top: 120,
    right: 390,
    bottom: 520,
    width: 390,
    height: 400,
  });
  assert.deepEqual(occlusionOf(frame, visible), { top: 68, left: 0, right: 0, bottom: 324 });
});

test('a box entirely off screen reports no usable area rather than a negative one', () => {
  const visible = visibleRect({ left: 0, top: 0, width: 390, height: 300 });
  const below = { left: 0, top: 400, right: 390, bottom: 700 };
  const usable = intersectRect(below, visible);
  assert.equal(usable.width, 390);
  assert.equal(usable.height, 0);
  // The occlusion of an edge never exceeds the box it is measured inside.
  assert.deepEqual(occlusionOf(below, visible), { top: 0, left: 0, right: 0, bottom: 300 });
});

test('a frame inside the visible rectangle has no insets to subtract again', () => {
  // Once the host has sized the frame to the usable box, the app must not
  // subtract the host header or the keyboard a second time.
  const visible = visibleRect({ left: 0, top: 0, width: 390, height: 400 });
  const frame = { left: 0, top: 52, right: 390, bottom: 400 };
  assert.deepEqual(occlusionOf(frame, visible), { top: 0, left: 0, right: 0, bottom: 0 });
});

test('a keyboard is only reported with a text entry focused and no zoom', () => {
  const geometry = { left: 0, top: 0, width: 390, height: 500, layoutWidth: 390, layoutHeight: 844, scale: 1, measured: true };
  assert.equal(describeViewport(geometry, { keyboardCandidate: true }).keyboardInset, 344);
  // The same rectangle without focus is a browser toolbar, not a keyboard.
  assert.equal(describeViewport(geometry, { keyboardCandidate: false }).keyboardInset, 0);
  // Pinch zoom shrinks the visible rectangle just as hard; it occludes nothing.
  const zoomed = { ...geometry, scale: 2.2 };
  const state = describeViewport(zoomed, { keyboardCandidate: true });
  assert.equal(state.zoomed, true);
  assert.equal(state.keyboardInset, 0);
});

test('a gap smaller than a keyboard is left to the browser', () => {
  const layoutHeight = 844;
  const geometry = {
    left: 0,
    top: 0,
    width: 390,
    height: layoutHeight - (KEYBOARD_MIN_PX - 1),
    layoutWidth: 390,
    layoutHeight,
    scale: 1,
    measured: true,
  };
  assert.equal(describeViewport(geometry, { keyboardCandidate: true }).keyboardInset, 0);
});

test('a browser that already resized the layout is not charged twice', () => {
  // Chrome with interactive-widget=resizes-content: innerHeight already excludes
  // the keyboard, so there is no remaining gap to subtract.
  const geometry = { left: 0, top: 0, width: 390, height: 500, layoutWidth: 390, layoutHeight: 500, scale: 1, measured: true };
  assert.equal(describeViewport(geometry, { keyboardCandidate: true }).keyboardInset, 0);
});

test('a missing VisualViewport still produces a usable layout', () => {
  const geometry = readGeometry({ innerWidth: 1280, innerHeight: 800 });
  assert.deepEqual(geometry, {
    left: 0,
    top: 0,
    width: 1280,
    height: 800,
    scale: 1,
    layoutWidth: 1280,
    layoutHeight: 800,
    measured: false,
  });
  const state = describeViewport(geometry, { keyboardCandidate: true });
  assert.equal(state.measured, false);
  assert.equal(state.keyboardInset, 0);
  assert.equal(viewportVariables(state)['--vela-visible-height'], '800px');
});

test('zoom keeps the published height at the layout viewport so the page can be panned', () => {
  const state = describeViewport(
    { left: 40, top: 200, width: 195, height: 422, layoutWidth: 390, layoutHeight: 844, scale: 2, measured: true },
    { keyboardCandidate: false },
  );
  assert.deepEqual(viewportVariables(state), {
    '--vela-visible-height': '844px',
    '--vela-visible-top': '0px',
    '--vela-keyboard-inset': '0px',
  });
});

test('focus is classified without reading an app frame', () => {
  assert.equal(hasTextEntryFocus({ activeElement: field('text') }), true);
  assert.equal(hasTextEntryFocus({ activeElement: field('email') }), true);
  assert.equal(hasTextEntryFocus({ activeElement: field('checkbox') }), false);
  assert.equal(hasTextEntryFocus({ activeElement: field('submit') }), false);
  assert.equal(hasTextEntryFocus({ activeElement: { tagName: 'TEXTAREA' } }), true);
  assert.equal(hasTextEntryFocus({ activeElement: { tagName: 'DIV', isContentEditable: true } }), true);
  assert.equal(hasTextEntryFocus({ activeElement: { tagName: 'BUTTON' } }), false);
  assert.equal(hasTextEntryFocus({ activeElement: null }), false);
  // A focused app frame counts: its document is protected, so the host cannot
  // see the field and must not assume the keyboard is closed.
  assert.equal(hasTextEntryFocus({ activeElement: { tagName: 'IFRAME' } }), true);
});

test('repeated viewport events produce one measurement per frame', () => {
  const env = fakeWindow();
  const store = createViewportStore(env);
  const seen = [];
  store.subscribe((state) => seen.push(state.height));
  for (let i = 0; i < 10; i++) env.emit('visual:resize');
  assert.equal(env.queued, 1, 'events coalesce into a single scheduled measurement');
  env.window.visualViewport.height = 500;
  env.document.activeElement = field();
  env.flush();
  assert.deepEqual(seen, [500]);
  store.dispose();
});

test('consumers are left alone when nothing they can see changed', () => {
  const env = fakeWindow();
  const store = createViewportStore(env);
  let notifications = 0;
  store.subscribe(() => notifications++);
  // Sub-pixel jitter from a scrolling browser toolbar.
  env.window.visualViewport.height = 844.3;
  assert.equal(store.refresh(), false);
  assert.equal(notifications, 0);
  env.window.visualViewport.height = 700;
  assert.equal(store.refresh(), true);
  assert.equal(notifications, 1);
  store.dispose();
});

test('the store publishes CSS variables and revises them on change', () => {
  const env = fakeWindow();
  const store = createViewportStore(env);
  assert.equal(env.style.get('--vela-keyboard-inset'), '0px');
  assert.equal(env.style.get('--vela-visible-height'), '844px');
  env.document.activeElement = { tagName: 'TEXTAREA' };
  env.window.visualViewport.height = 480;
  env.emit('document:focusin');
  env.flush();
  assert.equal(env.style.get('--vela-visible-height'), '480px');
  assert.equal(env.style.get('--vela-keyboard-inset'), '364px');
  // Dismissing the keyboard puts the space back.
  env.document.activeElement = null;
  env.window.visualViewport.height = 844;
  env.emit('document:focusout');
  env.flush();
  assert.equal(env.style.get('--vela-keyboard-inset'), '0px');
  store.dispose();
});

test('disposal removes every listener and cancels pending work', () => {
  const env = fakeWindow();
  const store = createViewportStore(env);
  assert.equal(env.bound, 6, 'visual resize/scroll, window resize/orientation, focus in/out');
  const unsubscribe = store.subscribe(() => {});
  assert.equal(store.listenerCount, 1);
  unsubscribe();
  assert.equal(store.listenerCount, 0);
  env.emit('window:resize');
  store.dispose();
  assert.equal(env.bound, 0);
  assert.equal(env.flush(), 0, 'the scheduled measurement was cancelled');
  // Mounting and unmounting repeatedly must not accumulate subscriptions.
  for (let i = 0; i < 5; i++) createViewportStore(env).dispose();
  assert.equal(env.bound, 0);
});

test('a store without VisualViewport still binds and reports the layout', () => {
  const env = fakeWindow({ visual: null });
  const store = createViewportStore(env);
  assert.equal(store.getState().measured, false);
  assert.equal(env.style.get('--vela-visible-height'), '844px');
  env.window.innerHeight = 600;
  env.emit('window:resize');
  env.flush();
  assert.equal(env.style.get('--vela-visible-height'), '600px');
  store.dispose();
  assert.equal(env.bound, 0);
});

test('viewportChanged ignores rounding and notices a real move', () => {
  const base = describeViewport(readGeometry({ innerWidth: 390, innerHeight: 844 }), {});
  assert.equal(viewportChanged(null, base), true);
  assert.equal(viewportChanged(base, { ...base, height: base.height + 0.4 }), false);
  assert.equal(viewportChanged(base, { ...base, height: base.height - 2 }), true);
  assert.equal(viewportChanged(base, { ...base, zoomed: true }), true);
  assert.equal(viewportChanged(base, { ...base, scale: 1.5 }), true);
});
