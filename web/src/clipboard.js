// One door for the clipboard and for opening a second window.
//
// `navigator.clipboard` does not exist in an insecure context, and a Vela
// server is reached over plain HTTP on the home network as often as not — so
// on the phone-handoff and install screens, which are exactly the screens that
// offer a "copy this link" button, the modern API is simply undefined. The
// selection-and-execCommand fallback is deprecated but still works there, and
// it is the difference between a copy button that works and one that silently
// does nothing.
//
// Keep this file and `storage.js` the only places that name these browser
// APIs; `web/scripts/ratchets/browser-boundary.mjs` enforces it.

function copyBySelection(text) {
  const field = document.createElement('textarea');
  field.value = text;
  // Off-screen rather than hidden: the selection has to be real for the copy
  // command to see it, and the page must not scroll to it.
  field.setAttribute('readonly', '');
  field.style.position = 'fixed';
  field.style.top = '-1000px';
  field.style.opacity = '0';
  document.body.appendChild(field);
  try {
    field.select();
    field.setSelectionRange(0, text.length);
    return document.execCommand('copy');
  } catch {
    return false;
  } finally {
    field.remove();
  }
}

/**
 * Copy `text`, resolving to whether it worked. Callers show their own message;
 * every one of them already has a "select it yourself" line for the false case.
 */
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return copyBySelection(text);
  }
}

/**
 * Open `url` in another tab. `noopener` is not optional: without it the new tab
 * gets a handle on this one through `window.opener`.
 */
export function openExternal(url, { features = 'noopener', target = '_blank' } = {}) {
  return window.open(url, target, features);
}
