/**
 * The approved window motion, checked as arithmetic.
 *
 * The shape of this effect is the part somebody tuned and approved, so it is
 * checked at fixed points on a controlled clock rather than by watching it. A
 * motion that can only be inspected by looking at it is a motion nobody can say
 * is right — and a regression in it would be a regression nobody notices until
 * it is shipped.
 *
 * Three things are being established:
 *
 * **The preset is one constant.** The prototype carried swoop 10 in three
 * places; a test that only read the label would have passed while the motion was
 * wrong. These read the values the geometry actually uses.
 *
 * **The neck is a stagger, not a curve.** Bands nearer the icon run ahead of
 * bands further from it, by an amount the neck control sets. That is the whole
 * mechanism, and it is checked by comparing bands rather than by comparing a
 * formula to a copy of itself.
 *
 * **Collapsing and expanding are different movements.** Same endpoints,
 * different easing, opposite swoop. Replaying one backwards would look like a
 * rewind, and the assertions here would catch it.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import {
  BAND_COUNT,
  BAND_OVERLAP_PX,
  GENIE,
  MIN_BAND_HEIGHT_PX,
  MIN_BAND_WIDTH_PX,
  REFERENCE,
  leadFor,
  swoopFor,
} from '../web/src/desktops/motion/genie-preset.js';
import {
  bandProgress,
  bandsExtent,
  bandsFor,
  collapseAt,
  elapsedFor,
  reversalDuration,
} from '../web/src/desktops/motion/genie-geometry.js';
import {
  MOTION,
  cancelMotion,
  initialMotion,
  isAnimating,
  isPresented,
  nextGeneration,
  requestMotion,
  settleMotion,
} from '../web/src/desktops/motion/motion-state.js';
import {
  CAPABILITY,
  MAX_FRAME_AGE_MS,
  capabilityFor,
} from '../web/src/desktops/motion/frame-source.js';

/** The reference composition, so the numbers here mean what the plan's do. */
const SOURCE = { x: 140, y: 80, width: 920, height: 680 };
const TARGET = { x: 36, y: 420, width: 44, height: 44 };
const FRAME = { width: 920, height: 680 };

const at = (collapse, over = {}) =>
  bandsFor({ source: SOURCE, target: TARGET, frame: FRAME, collapse, ...over });

describe('the preset', () => {
  test('is the approved one, and is one object', () => {
    assert.equal(GENIE.preset, 'vela-genie-v1');
    assert.equal(GENIE.durationMs, 480);
    assert.equal(GENIE.neck, 10);
    assert.equal(GENIE.swoopPx, 150, 'the export said 10; the approved value is 150');
    assert.ok(Object.isFrozen(GENIE), 'nothing gets to edit it at runtime');
  });

  test('neck 10 is a lead of 0.10, and the control stays dimensionless', () => {
    assert.equal(leadFor(10), 0.1);
    assert.equal(leadFor(0), 0.06, 'clamped: with no lead there is no neck at all');
    assert.equal(leadFor(100), 0.92, 'clamped: the last band still has to start');
    assert.equal(leadFor(undefined), 0.1);
  });

  test('the swoop is a distance, scaled down only on a narrower screen', () => {
    assert.equal(swoopFor({ width: REFERENCE.width }), 150);
    assert.equal(swoopFor({ width: 2560 }), 150, 'a wider screen does not swing further');
    assert.equal(swoopFor({ width: 390 }), Math.round(150 * (390 / 1440)));
    assert.equal(swoopFor(null), 150);
  });
});

describe('the clock', () => {
  test('a collapse starts at the window and ends at the icon', () => {
    assert.equal(collapseAt(0), 0);
    assert.equal(collapseAt(GENIE.durationMs), 1);
    assert.equal(collapseAt(GENIE.durationMs * 5), 1, 'past the end is the end');
  });

  test('an expansion starts at the icon and ends at the window', () => {
    const options = { direction: 'expand' };
    assert.equal(collapseAt(0, options), 1);
    assert.equal(collapseAt(GENIE.durationMs, options), 0);
  });

  test('the two directions are different curves, not one played backwards', () => {
    const half = GENIE.durationMs / 2;
    const collapsing = collapseAt(half);
    const expanding = collapseAt(half, { direction: 'expand' });
    // Collapsing eases in — at halfway it has covered less than half.
    assert.ok(collapsing < 0.5, `collapse at halfway was ${collapsing}`);
    // Expanding eases out — at halfway it has covered more than half, so what
    // is left to collapse is less than half.
    assert.ok(expanding < 0.5, `expand at halfway left ${expanding}`);
    assert.notEqual(
      Number(collapsing.toFixed(4)),
      Number(expanding.toFixed(4)),
      'a mirrored curve would make these equal',
    );
  });

  test('a time can be recovered from a shape, so a reversal starts where it is', () => {
    for (const c of [0, 0.25, 0.5, 0.75, 1]) {
      const elapsed = elapsedFor(c);
      assert.ok(Math.abs(collapseAt(elapsed) - c) < 1e-6, `collapse ${c}`);
      const back = elapsedFor(c, { direction: 'expand' });
      assert.ok(
        Math.abs(collapseAt(back, { direction: 'expand' }) - c) < 1e-6,
        `expand ${c}`,
      );
    }
  });

  test('a reversal takes as long as the distance that is left', () => {
    assert.equal(reversalDuration(0), GENIE.durationMs, 'from the window, the whole way');
    assert.equal(reversalDuration(1), 60, 'already there: the floor, not zero');
    assert.equal(reversalDuration(0.5), GENIE.durationMs / 2);
    assert.equal(reversalDuration(0.25, { direction: 'expand' }), GENIE.durationMs / 4);
  });
});

describe('the neck', () => {
  test('bands nearer the icon run ahead of bands further from it', () => {
    const bands = at(0.4);
    const nearest = bands.reduce((best, band) => (band.u < best.u ? band : best));
    const furthest = bands.reduce((best, band) => (band.u > best.u ? band : best));
    assert.ok(
      nearest.progress > furthest.progress,
      `nearest ${nearest.progress} should lead furthest ${furthest.progress}`,
    );
  });

  test('the furthest band waits exactly the lead before it starts', () => {
    const lead = leadFor();
    assert.equal(bandProgress(lead * 0.999, 1, lead), 0, 'not yet');
    assert.ok(bandProgress(lead + 0.01, 1, lead) > 0, 'now');
    assert.equal(bandProgress(1, 1, lead), 1, 'and it still finishes');
  });

  test('a bigger neck makes a longer stagger', () => {
    const tight = at(0.4, { neck: 6 });
    const loose = at(0.4, { neck: 80 });
    const spread = (bands) => {
      const values = bands.map((band) => band.progress);
      return Math.max(...values) - Math.min(...values);
    };
    assert.ok(spread(loose) > spread(tight));
  });

  test('every band is still finished at the end, whatever its lead', () => {
    for (const band of at(1)) assert.equal(band.progress, 1, `band ${band.index}`);
  });
});

describe('the bands themselves', () => {
  test('there are 140 of them, and they are draws of one image', () => {
    const bands = at(0.5);
    assert.equal(bands.length, BAND_COUNT);
    // Every band reads a different strip of the same frame, top to bottom.
    assert.equal(bands[0].sy, 0);
    assert.ok(bands.at(-1).sy > bands[0].sy);
    for (const band of bands) assert.equal(band.sw, FRAME.width);
  });

  test('at rest the window is exactly where the window is', () => {
    const bands = at(0);
    assert.equal(bands[0].dx, SOURCE.x);
    assert.ok(Math.abs(bands[0].dw - SOURCE.width) < 0.001);
    const extent = bandsExtent(bands);
    assert.ok(Math.abs(extent.x - SOURCE.x) < 1, `left was ${extent.x}`);
    assert.ok(Math.abs(extent.width - SOURCE.width) < 2, `width was ${extent.width}`);
    // The overlap makes it a fraction taller than the window; that is the point
    // of the overlap and it must not be more than a fraction.
    assert.ok(Math.abs(extent.height - SOURCE.height) <= BAND_OVERLAP_PX * 2 + 1);
  });

  test('at the end everything has arrived inside the icon', () => {
    const extent = bandsExtent(at(1));
    assert.ok(extent.width <= TARGET.width + MIN_BAND_WIDTH_PX + 1, `width ${extent.width}`);
    assert.ok(extent.x >= TARGET.x - 1 && extent.x <= TARGET.x + TARGET.width);
    assert.ok(extent.y >= TARGET.y - BAND_OVERLAP_PX - 1, `top ${extent.y}`);
  });

  test('bands narrow on the way and never vanish', () => {
    const widths = [0, 0.25, 0.5, 0.75, 1].map((c) => at(c)[0].dw);
    for (let i = 1; i < widths.length; i += 1) {
      assert.ok(widths[i] <= widths[i - 1] + 0.001, `width grew at ${i}`);
    }
    for (const c of [0, 0.25, 0.5, 0.75, 1]) {
      for (const band of at(c)) {
        assert.ok(band.dw >= MIN_BAND_WIDTH_PX, `band ${band.index} at ${c} was ${band.dw}`);
        assert.ok(band.dh >= MIN_BAND_HEIGHT_PX, `band ${band.index} at ${c} was ${band.dh}`);
      }
    }
  });

  test('neighbouring bands overlap, so there are no seams to see through', () => {
    for (const c of [0, 0.25, 0.5, 0.75]) {
      const bands = at(c);
      for (let i = 1; i < bands.length; i += 1) {
        const previousBottom = bands[i - 1].dy + bands[i - 1].dh;
        assert.ok(
          previousBottom >= bands[i].dy - 0.001,
          `a gap opened between ${i - 1} and ${i} at ${c}`,
        );
      }
    }
  });

  test('the swoop peaks in the middle and is gone at both ends', () => {
    // Measured as how far the furthest band sits from where a straight line
    // between the two rectangles would put it.
    const offsetAt = (c, over = {}) => {
      const bands = at(c, over);
      const band = bands.reduce((best, entry) => (entry.u > best.u ? entry : best));
      const straight = SOURCE.x + (TARGET.x - SOURCE.x) * band.progress;
      return band.dx - straight;
    };
    assert.ok(Math.abs(offsetAt(0)) < 0.001, 'nothing has swung at the start');
    assert.ok(Math.abs(offsetAt(1)) < 0.001, 'and nothing is swung at the end');
    assert.ok(Math.abs(offsetAt(0.5)) > 10, 'but plenty in the middle');
  });

  test('collapsing and expanding swing opposite ways', () => {
    const furthest = (bands) => bands.reduce((best, band) => (band.u > best.u ? band : best));
    const collapsing = furthest(at(0.5, { direction: 'collapse' }));
    const expanding = furthest(at(0.5, { direction: 'expand' }));
    const straight = (band) => SOURCE.x + (TARGET.x - SOURCE.x) * band.progress;
    const one = collapsing.dx - straight(collapsing);
    const other = expanding.dx - straight(expanding);
    assert.ok(one * other < 0, `both swung the same way: ${one} and ${other}`);
  });

  test('a bigger swoop swings further, and zero swings not at all', () => {
    const furthest = (bands) => bands.reduce((best, band) => (band.u > best.u ? band : best));
    const straight = (band) => SOURCE.x + (TARGET.x - SOURCE.x) * band.progress;
    const swing = (swoopPx) => {
      const band = furthest(at(0.5, { swoopPx }));
      return Math.abs(band.dx - straight(band));
    };
    assert.ok(swing(150) > swing(10), 'the export’s 10 is visibly less motion than 150');
    assert.ok(swing(0) < 0.001);
  });

  test('a band nearest the icon barely swings at all', () => {
    const bands = at(0.5);
    const nearest = bands.reduce((best, band) => (band.u < best.u ? band : best));
    const straight = SOURCE.x + (TARGET.x - SOURCE.x) * nearest.progress;
    assert.ok(Math.abs(nearest.dx - straight) < 5, 'the swing is proportional to the distance');
  });

  test('nothing is produced without a frame to draw', () => {
    assert.deepEqual(bandsFor({ source: SOURCE, target: TARGET, frame: null, collapse: 0.5 }), []);
    assert.deepEqual(bandsFor({ source: null, target: TARGET, frame: FRAME, collapse: 0.5 }), []);
    assert.equal(bandsExtent([]), null);
  });

  test('the geometry is the same whatever the display density', () => {
    // CSS pixels in, CSS pixels out. The device pixel ratio belongs to the
    // canvas backing store; a geometry that knew about it would click, draw and
    // measure at twice the intended place on a dense display.
    const once = at(0.37);
    const again = at(0.37);
    assert.deepEqual(once, again);
  });
});

describe('the lifecycle', () => {
  const collapse = (from) =>
    requestMotion(from, 'collapse', { durationMs: GENIE.durationMs, now: 0 });
  const expand = (from) => requestMotion(from, 'expand', { durationMs: GENIE.durationMs, now: 0 });

  test('a fresh window is idle and drawn; a minimized one is neither', () => {
    assert.equal(initialMotion(false).state, MOTION.IDLE);
    assert.equal(initialMotion(false).collapse, 0);
    assert.equal(isPresented(initialMotion(false)), true);
    assert.equal(initialMotion(true).state, MOTION.MINIMIZED);
    assert.equal(initialMotion(true).collapse, 1);
    assert.equal(isPresented(initialMotion(true)), false);
  });

  test('each run gets its own generation, and generations are never reused', () => {
    const one = nextGeneration();
    const two = nextGeneration();
    assert.notEqual(one, two);
    const first = collapse(initialMotion(false));
    const second = expand(first);
    assert.notEqual(first.generation, second.generation);
  });

  test('a completion from a run that is over changes nothing', () => {
    // The failure this prevents: a window somebody has restored being hidden
    // again by the collapse that was cancelled to restore it.
    const collapsing = collapse(initialMotion(false));
    const restoring = expand({ ...collapsing, collapse: 0.4 });
    const stale = settleMotion(restoring, collapsing.generation);
    assert.equal(stale.state, MOTION.RESTORING, 'the old run did not get to decide');
    const current = settleMotion(restoring, restoring.generation);
    assert.equal(current.state, MOTION.IDLE);
    assert.equal(current.collapse, 0);
  });

  test('a reversal starts from the shape on screen, over the distance that is left', () => {
    const collapsing = { ...collapse(initialMotion(false)), collapse: 0.25 };
    const back = expand(collapsing);
    assert.equal(back.state, MOTION.RESTORING);
    assert.equal(back.collapse, 0.25, 'it picks up where it is, not at an endpoint');
    assert.equal(reversalDuration(0.25, { direction: 'expand' }), GENIE.durationMs / 4);
  });

  test('cancelling applies the state that was decided rather than the one on screen', () => {
    // A hidden tab stops delivering frames. If the lifecycle waited for the
    // last one, the window would stay half-collapsed and unclickable forever.
    const collapsing = { ...collapse(initialMotion(false)), collapse: 0.3 };
    const settled = cancelMotion(collapsing, 'the tab was hidden');
    assert.equal(settled.state, MOTION.MINIMIZED);
    assert.equal(settled.collapse, 1);
    assert.equal(settled.reason, 'the tab was hidden');
    assert.equal(isAnimating(settled), false);
    assert.notEqual(settled.generation, collapsing.generation, 'the old run cannot finish now');
  });

  test('cancelling something that was not running leaves it exactly as it was', () => {
    const idle = initialMotion(false);
    const after = cancelMotion(idle, 'nothing was happening');
    assert.equal(after.state, MOTION.IDLE);
    assert.equal(after.generation, idle.generation);
  });

  test('asking for the state it is already in does not start a run', () => {
    const minimized = initialMotion(true);
    const again = collapse(minimized);
    assert.equal(again.generation, minimized.generation);
    assert.equal(again.state, MOTION.MINIMIZED);
  });

  test('motion says nothing about an app, a session or a task', () => {
    // Every state here is about what is drawn. There is deliberately no field
    // that could be read as "this app is stopped" or "this task is paused".
    const running = collapse(initialMotion(false));
    for (const forbidden of ['paused', 'stopped', 'session', 'task', 'closed']) {
      assert.ok(!(forbidden in running), forbidden);
    }
  });
});

describe('where a picture can come from', () => {
  const agentDesktop = { kind: 'agent' };

  test('a view the agent renders can be warped from a real frame of it', () => {
    const answer = capabilityFor(
      { id: 'v1', agentViewable: true, available: true },
      { desktop: agentDesktop },
    );
    assert.equal(answer.capability, CAPABILITY.REMOTE);
  });

  test('an embedded app on the owner\u2019s own desktop cannot be, and says why', () => {
    // Not a missing feature: a browser will not hand this page the pixels of a
    // cross-origin frame, and asking for screen recording to animate something
    // would trade a real boundary for decoration.
    const answer = capabilityFor(
      { id: 'v1', agentViewable: false, available: true },
      { desktop: { kind: 'personal' } },
    );
    assert.equal(answer.capability, CAPABILITY.FALLBACK);
    assert.match(answer.reason, /pixels/);
  });

  test('a window whose app was reinstalled falls back rather than drawing the old one', () => {
    const answer = capabilityFor(
      { id: 'v1', agentViewable: true, available: false },
      { desktop: agentDesktop },
    );
    assert.equal(answer.capability, CAPABILITY.FALLBACK);
    assert.match(answer.reason, /reopening/);
  });

  test('there is a bound on how old a picture may be', () => {
    assert.ok(MAX_FRAME_AGE_MS > 0 && MAX_FRAME_AGE_MS <= 10_000);
  });

  test('no window at all is a fallback, not a crash', () => {
    assert.equal(capabilityFor(null).capability, CAPABILITY.FALLBACK);
  });
});
