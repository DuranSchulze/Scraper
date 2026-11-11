"use client";

import { useSyncExternalStore } from "react";

// The theme lives on <html data-theme>, set before first paint by the inline script in app/layout.tsx.
export type Theme = "dark" | "light";
const KEY = "scraper.theme";
const EVENT = "scraper:theme";

const read = (): Theme => (document.documentElement.dataset.theme === "light" ? "light" : "dark");

// Instant switch, like GitHub/Vercel/Linear: one attribute flip restyles the page once (~20 ms). A full-page fade
// (GSAP tween or View Transition crossfade) measured 30 fps on big pages, which read as lag.
const apply = (next: Theme) => { document.documentElement.dataset.theme = next; };

export function toggleTheme() {
  const next: Theme = read() === "dark" ? "light" : "dark";
  apply(next);
  try { localStorage.setItem(KEY, next); } catch { /* storage unavailable */ }
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(cb: () => void) {
  const onStorage = (e: StorageEvent) => {
    if (e.key !== KEY) return;
    apply(e.newValue === "light" ? "light" : "dark");
    cb();
  };
  window.addEventListener(EVENT, cb);
  window.addEventListener("storage", onStorage);
  return () => { window.removeEventListener(EVENT, cb); window.removeEventListener("storage", onStorage); };
}

export const useTheme = (): Theme => useSyncExternalStore(subscribe, read, () => "dark");
