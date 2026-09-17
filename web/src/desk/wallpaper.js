import { useEffect, useState } from 'react';

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

// The wallpaper flags that select the picture. Kept apart from the `desk` flag
// so the Desk page, which owns that flag for its own reasons, can share this.
//
// `desk.customUrl` is where this desktop's own image is served from. It is a
// CSS variable rather than a rule because each desktop has its own picture at
// its own address, which a stylesheet cannot know.
export function useWallpaperFlags(desk) {
  const choice = desk?.wallpaper || DEFAULT_WALLPAPER;
  useDailyTick(choice === 'daily');
  const { id, tone } = resolveWallpaper(desk);
  const dim = desk?.dim === false ? 'off' : 'on';
  const customUrl = desk?.customUrl || null;
  useEffect(() => {
    document.body.dataset.deskWallpaper = id;
    document.body.dataset.deskChoice = choice;
    document.body.dataset.deskDim = dim;
    if (tone) document.body.dataset.deskTone = tone;
    else delete document.body.dataset.deskTone;
    if (customUrl)
      document.body.style.setProperty('--desk-wallpaper-custom', `url('${customUrl}')`);
    else document.body.style.removeProperty('--desk-wallpaper-custom');
    return () => {
      delete document.body.dataset.deskWallpaper;
      delete document.body.dataset.deskChoice;
      delete document.body.dataset.deskDim;
      delete document.body.dataset.deskTone;
      document.body.style.removeProperty('--desk-wallpaper-custom');
    };
  }, [id, choice, dim, tone, customUrl]);
}

// The wallpaper belongs to the whole shell, not to one page: it sits behind the
// translucent rail as well as the workspace. Both the Desk and the Launchpad
// render over it, so the body flags that select it live here rather than in
// either page. The flags are cleared on the way out so a plain page never
// inherits the wallpaper treatment.
export function useWallpaperBody(desk) {
  useEffect(() => {
    document.body.dataset.desk = 'on';
    return () => {
      delete document.body.dataset.desk;
    };
  }, []);
  useWallpaperFlags(desk);
}
