"use client";

import { useSyncExternalStore } from "react";
import gsap from "gsap";

// The theme lives on <html data-theme>, set before first paint by the inline script in app/layout.tsx.
export type Theme = "dark" | "light";
const KEY = "scraper.theme";
const EVENT = "scraper:theme";

// Every colour token the themes define in globals.css. A switch tweens these custom properties on <html> only,
// so the browser restyles once per frame instead of running a CSS transition on every element.
const TOKENS = [
  "background", "foreground", "card", "card-foreground", "popover", "popover-foreground", "primary", "primary-foreground",
  "primary-text", "notify", "secondary", "secondary-foreground", "muted", "muted-foreground", "accent", "accent-foreground",
  "destructive", "border", "input", "ring", "chart-1", "chart-2", "chart-3", "chart-4", "chart-5",
  "sidebar", "sidebar-foreground", "sidebar-primary", "sidebar-primary-foreground", "sidebar-accent",
  "sidebar-accent-foreground", "sidebar-border", "sidebar-ring", "success", "warning", "info",
].map((t) => `--${t}`);

const DURATION = 0.45;
// Restyling the whole page costs about 10 ms per frame, which is most of a 60 fps frame budget, so a 60 fps tween
// drops frames unevenly. Painting at ~30 fps keeps every frame inside its budget and the pacing even.
const MIN_STEP_MS = 28;
type Colors = Record<string, string>;
// The running switch: one GSAP tween of a 0..1 progress value; the colours are interpolated in JS from cached values,
// so retargeting mid-switch needs no DOM reads (and no forced style recalculation).
// `shown` is the progress last written to the page: with the repaint cap, `p` can be ahead of what is on screen.
let anim: { tween: gsap.core.Tween; fns: Record<string, (p: number) => string>; p: { v: number }; shown: { v: number } } | undefined;

const read = (): Theme => (document.documentElement.dataset.theme === "light" ? "light" : "dark");

const current = (root: HTMLElement): Colors => {
  const style = getComputedStyle(root);
  return Object.fromEntries(TOKENS.map((t) => [t, style.getPropertyValue(t).trim()]));
};
const clearInline = (root: HTMLElement) => TOKENS.forEach((t) => root.style.removeProperty(t));

// Each theme's stylesheet colours, read once. Reading forces a style recalculation of the whole page, so doing it
// during a click would cost a dropped frame; prewarmTheme() does it while the page is idle.
const cache: Partial<Record<Theme, Colors>> = {};
function themeColors(theme: Theme): Colors {
  const root = document.documentElement;
  const cached = cache[theme];
  if (cached) return cached;
  const previous = root.dataset.theme;
  const inline = root.getAttribute("style");
  clearInline(root);
  root.dataset.theme = theme;
  const colors = current(root);
  root.dataset.theme = previous;
  if (inline) root.setAttribute("style", inline);
  return (cache[theme] = colors);
}

export function prewarmTheme() {
  if (anim) return;
  themeColors("dark");
  themeColors("light");
}

function apply(next: Theme, animate: boolean) {
  const root = document.documentElement;
  if (!animate || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    anim?.tween.kill();
    anim = undefined;
    clearInline(root);
    root.dataset.theme = next;
    return;
  }
  // Start from the colours on screen now (the cached theme, or the half-finished switch), aim at the new theme.
  const from: Colors = anim
    ? Object.fromEntries(TOKENS.map((t) => [t, anim!.fns[t](anim!.shown.v)]))
    : themeColors(read());
  const to = themeColors(next);
  anim?.tween.kill();
  root.dataset.theme = next;
  const fns = Object.fromEntries(TOKENS.map((t) => [t, gsap.utils.interpolate(from[t], to[t]) as (p: number) => string]));
  const p = { v: 0 };
  const shown = { v: 0 };
  const write = () => {
    shown.v = p.v;
    TOKENS.forEach((t) => root.style.setProperty(t, fns[t](shown.v)));
  };
  write();
  let last = performance.now();
  const tween = gsap.to(p, {
    v: 1,
    duration: DURATION,
    ease: "power2.inOut",
    onUpdate: () => {
      const now = performance.now();
      if (now - last >= MIN_STEP_MS) { last = now; write(); }
    },
    // Hand control back to the stylesheet, so the resting state is exactly the theme's own values.
    onComplete: () => { clearInline(root); anim = undefined; },
  });
  anim = { tween, fns, p, shown };
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
