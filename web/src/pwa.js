// PWA helpers: iOS detection, standalone detection, and a shared store for the
// beforeinstallprompt event so both the hub header and app drawers can offer
// an install button where the browser supports it.

import { useSyncExternalStore } from 'react';

let deferredPrompt = null;
const listeners = new Set();

function notify() {
  listeners.forEach((fn) => fn());
}

if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    deferredPrompt = event;
    notify();
  });
  window.addEventListener('appinstalled', () => {
    deferredPrompt = null;
    notify();
  });
}

function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function useInstallPrompt() {
  return useSyncExternalStore(subscribe, () => deferredPrompt, () => null);
}

export async function promptInstall(event) {
  if (!event) return null;
  event.prompt();
  const choice = await event.userChoice;
  if (choice.outcome === 'accepted') {
    deferredPrompt = null;
    notify();
  }
  return choice.outcome;
}

// iPhone / iPad / iPod, including iPadOS 13+ reporting as Macintosh.
// All iOS browsers (Safari, CriOS, etc.) share WebKit and none expose an
// install prompt API, so they all get the manual instructions.
export function isIOS() {
  if (typeof navigator === 'undefined') return false;
  const ua = navigator.userAgent;
  return /iP(hone|ad|od)/.test(ua) || (ua.includes('Macintosh') && navigator.maxTouchPoints > 1);
}

export function isStandalone() {
  if (typeof window === 'undefined') return false;
  return window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
}

export function registerServiceWorker() {
  // Prod only: in dev the Vite server and its proxy should always be live,
  // and a caching SW would fight hot module reload.
  if (!import.meta.env.PROD || !('serviceWorker' in navigator)) return;
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // Install/offline support is best-effort; the hub works without it.
    });
  });
}
