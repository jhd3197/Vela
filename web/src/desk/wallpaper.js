import { useEffect } from 'react';

// The wallpaper belongs to the whole shell, not to one page: it sits behind the
// translucent rail as well as the workspace. Both the Desk and the Launchpad
// render over it, so the body flags that select it live here rather than in
// either page. The flags are cleared on the way out so a plain page never
// inherits the wallpaper treatment.
export function useWallpaperBody(desk) {
  const wallpaper = desk?.wallpaper || 'lake';
  const dim = desk?.dim === false ? 'off' : 'on';
  useEffect(() => {
    document.body.dataset.desk = 'on';
    document.body.dataset.deskWallpaper = wallpaper;
    document.body.dataset.deskDim = dim;
    return () => {
      delete document.body.dataset.desk;
      delete document.body.dataset.deskWallpaper;
      delete document.body.dataset.deskDim;
    };
  }, [wallpaper, dim]);
}
