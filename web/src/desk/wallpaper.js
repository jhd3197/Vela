import { useEffect, useRef, useState } from 'react';

// The painted set that ships with Vela: eight places drawn for the desk rather
// than stock photography, so there is no licence to track and no face to
// recognise. `tone` says whether the picture is bright or dark overall, which
// nudges the overlay so widget text stays readable either way — measured from
// the images, not guessed.
export const BUNDLED_WALLPAPERS = [
  { id: 'choroni', name: 'Choroní', tone: 'light' },
  { id: 'paramo', name: 'Páramo', tone: 'dark' },
  { id: 'medanos', name: 'Médanos', tone: 'light' },
  { id: 'chiguire', name: 'Chigüire', tone: 'light' },
  { id: 'pueblo', name: 'Pueblo', tone: 'light' },
  { id: 'avila', name: 'Ávila', tone: 'dark' },
  { id: 'castillo', name: 'Castillo', tone: 'light' },
  { id: 'canaima', name: 'Canaima', tone: 'dark' },
];

// What a desk with no stored choice draws.
export const DEFAULT_WALLPAPER = 'choroni';

const TONES = new Map(BUNDLED_WALLPAPERS.map((wall) => [wall.id, wall.tone]));

// The day of the year, counted locally. Two machines in different time zones
// can disagree for a few hours; each is right about its own midnight, which is
// what the rotation promises.
export function dayOfYear(date = new Date()) {
  const start = new Date(date.getFullYear(), 0, 0);
  return Math.floor((date - start) / 86400000);
}

// Daily picks from the painted set only: gradients and the user's own image are
// deliberate choices rather than something to land on by date.
export function dailyWallpaper(date = new Date()) {
  return BUNDLED_WALLPAPERS[dayOfYear(date) % BUNDLED_WALLPAPERS.length].id;
}

// `daily` is a choice, not a picture. It resolves to one of the painted set for
// today while the sheet keeps showing Daily as the selected option, which is why
// the choice and the drawn id are returned separately.
export function resolveWallpaper(desk, date = new Date()) {
  const choice = desk?.wallpaper || DEFAULT_WALLPAPER;
  const id = choice === 'daily' ? dailyWallpaper(date) : choice;
  return { choice, id, tone: TONES.get(id) || '' };
}

// Milliseconds until the next local midnight, which is when Daily moves on.
function untilMidnight(now = new Date()) {
  const next = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  return Math.max(1000, next - now);
}

// A desk stays open for days at a time, so Daily cannot resolve once at mount
// and stop. This ticks at local midnight and only while Daily is chosen.
function useDailyTick(active) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!active) return undefined;
    let timer = 0;
    const schedule = () => {
      timer = setTimeout(() => {
        setTick((value) => value + 1);
        schedule();
      }, untilMidnight());
    };
    schedule();
    return () => clearTimeout(timer);
  }, [active]);
}

// ---------------------------------------------------------- who is asking
//
// The desk and the Launchpad are on screen at the same time: All apps opens
// *over* the desk rather than replacing it. Both want the same picture, and
// both used to write these flags straight onto `<body>` from an effect that
// cleared them again on the way out. Closing All apps therefore ran the
// Launchpad's cleanup while the desk underneath was still mounted and still
// expecting its wallpaper — and the desk's own effect, having already run and
// having no reason to run again, never put it back. The picture vanished and
// stayed gone until something unrelated changed.
//
// So this is a stack of claims rather than a write. Each owner claims what it
// wants drawn, the topmost claim is what is on the body, and releasing one
// repaints from whoever is left. Nothing is cleared while somebody is still
// asking, which is the property the effect-per-page version could not have.

/** `{ owner, flags }`, oldest first. The last one is what is drawn. */
const claims = [];

/** What is drawn right now, or null when nobody is asking. Read by tests. */
export function wallpaperClaim() {
  return claims.length ? claims[claims.length - 1].flags : null;
}

function paint() {
  const body = typeof document === 'undefined' ? null : document.body;
  if (!body) return;
  const flags = wallpaperClaim();
  if (!flags) {
    delete body.dataset.desk;
    delete body.dataset.deskWallpaper;
    delete body.dataset.deskChoice;
    delete body.dataset.deskDim;
    delete body.dataset.deskTone;
    body.style.removeProperty('--desk-wallpaper-custom');
    return;
  }
  body.dataset.desk = 'on';
  body.dataset.deskWallpaper = flags.id;
  body.dataset.deskChoice = flags.choice;
  body.dataset.deskDim = flags.dim;
  if (flags.tone) body.dataset.deskTone = flags.tone;
  else delete body.dataset.deskTone;
  // A CSS variable rather than a rule, because each desktop has its own
  // picture at its own address and a stylesheet cannot know it.
  if (flags.customUrl)
    body.style.setProperty('--desk-wallpaper-custom', `url('${flags.customUrl}')`);
  else body.style.removeProperty('--desk-wallpaper-custom');
}

/** Ask for a picture, or change what an existing claim is asking for. */
export function claimWallpaper(owner, flags) {
  const existing = claims.find((claim) => claim.owner === owner);
  // Updated in place: a desk that changed its wallpaper is the same owner
  // asking for something else, not a second owner arriving on top.
  if (existing) existing.flags = flags;
  else claims.push({ owner, flags });
  paint();
}

/** Stop asking. Whoever is still asking gets what they asked for. */
export function releaseWallpaper(owner) {
  const index = claims.findIndex((claim) => claim.owner === owner);
  if (index === -1) return;
  claims.splice(index, 1);
  paint();
}

/** The flags a desk's appearance implies, with nothing on the page touched. */
export function wallpaperFlags(desk) {
  const choice = desk?.wallpaper || DEFAULT_WALLPAPER;
  const { id, tone } = resolveWallpaper(desk);
  return {
    id,
    choice,
    tone,
    dim: desk?.dim === false ? 'off' : 'on',
    customUrl: desk?.customUrl || null,
  };
}

// The wallpaper belongs to the whole shell, not to one page: it sits behind the
// translucent rail as well as the workspace, and it has to survive a page that
// floats over another one. Both the Desk and the Launchpad claim it the same
// way, which is why the rules live here rather than in either page.
export function useWallpaperBody(desk) {
  const { id, choice, tone, dim, customUrl } = wallpaperFlags(desk);
  useDailyTick(choice === 'daily');
  // One identity per mounted owner, so re-running for a new wallpaper updates
  // this owner's claim rather than stacking a second one on top of it.
  const owner = useRef(null);
  if (!owner.current) owner.current = { page: true };
  useEffect(() => {
    claimWallpaper(owner.current, { id, choice, dim, tone, customUrl });
  }, [id, choice, dim, tone, customUrl]);
  useEffect(() => {
    const self = owner.current;
    return () => releaseWallpaper(self);
  }, []);
}
