import { useSyncExternalStore } from 'react';

// Whether a CSS media query currently matches, kept in step with the window.
export default function useMediaQuery(query) {
  return useSyncExternalStore(
    (notify) => {
      const list = matchMedia(query);
      list.addEventListener('change', notify);
      return () => list.removeEventListener('change', notify);
    },
    () => matchMedia(query).matches,
    () => false,
  );
}
