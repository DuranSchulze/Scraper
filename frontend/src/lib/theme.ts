"use client";

import { useSyncExternalStore } from "react";

// The theme lives on <html data-theme>, set before first paint by the inline script in app/layout.tsx.
export type Theme = "dark" | "light";
const KEY = "scraper.theme";
const EVENT = "scraper:theme";

const read = (): Theme => (document.documentElement.dataset.theme === "light" ? "light" : "dark");

let switchTimer: number | undefined;

function apply(next: Theme, animate: boolean) {
  const root = document.documentElement;
  // data-theme-switching enables a short colour cross-fade (globals.css); it is removed again so no other transition is affected.
  if (animate && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    root.dataset.themeSwitching = "";
    // Only the latest switch may end the cross-fade, so rapid toggles never cut one short.
    window.clearTimeout(switchTimer);
    switchTimer = window.setTimeout(() => { delete root.dataset.themeSwitching; }, 400);
  }
  root.dataset.theme = next;
}

export function toggleTheme() {
  const next: Theme = read() === "dark" ? "light" : "dark";
  apply(next, true);
  try { localStorage.setItem(KEY, next); } catch { /* storage unavailable */ }
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(cb: () => void) {
  const onStorage = (e: StorageEvent) => {
    if (e.key !== KEY) return;
    apply(e.newValue === "light" ? "light" : "dark", true);
    cb();
  };
  window.addEventListener(EVENT, cb);
  window.addEventListener("storage", onStorage);
  return () => { window.removeEventListener(EVENT, cb); window.removeEventListener("storage", onStorage); };
}

export const useTheme = (): Theme => useSyncExternalStore(subscribe, read, () => "dark");
