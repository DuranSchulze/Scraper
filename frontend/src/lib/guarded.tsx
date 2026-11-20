"use client";

import { useSyncExternalStore } from "react";
import { Loader2 } from "lucide-react";
import { onlineManager } from "@tanstack/react-query";
import { toast } from "sonner";

export const GRACE_SECONDS = 5;

type State = { phase: "idle" | "queued" | "running"; left: number; verb: string };
const IDLE: State = { phase: "idle", left: 0, verb: "" };

// Module-level, so the lock and the countdown survive the button unmounting (page change, closed record):
// coming back shows the write still queued or running, and it cannot be started a second time.
const states = new Map<string, State>();
const cancels = new Map<string, (message?: string) => void>();
const listeners = new Set<() => void>();
const set = (key: string, s: State) => { states.set(key, s); listeners.forEach((l) => l()); };
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; };
const get = (key: string) => states.get(key) ?? IDLE;
// Set once another tab signs in or out: this page's cookie may now belong to another account.
let sessionChanged = false;

// One listener for the page's lifetime: closing the tab while any write is queued or running asks first
// (a queued one would be dropped, a running one may not finish).
if (typeof window !== "undefined") {
  window.addEventListener("beforeunload", (e) => {
    if ([...states.values()].some((s) => s.phase !== "idle")) e.preventDefault();
  });
}

/**
 * Drops every queued write without sending it and refuses new ones from this page. Called when the session
 * changes (another tab signed in or out), so a write confirmed by one account is never sent with the next
 * account's cookie, even if the reload is declined.
 */
export function cancelQueued() {
  sessionChanged = true;
  [...cancels.values()].forEach((cancel) => cancel("Cancelled because the account changed. Nothing was sent."));
}

/**
 * A confirmed write waits GRACE_SECONDS with Undo and sends nothing until then; after that it runs to the end
 * and is never cancelled. While one is queued or running under `key`, another start is ignored.
 */
export function useGuarded(key: string) {
  const s = useSyncExternalStore(subscribe, () => get(key), () => IDLE);

  /** `verb` labels the button ("Publishing"), `what` the toast ("Publishing 12 records"). `run` must reject on failure. */
  const start = ({ verb, what, run, onUndo }: { verb: string; what: string; run: () => Promise<unknown>; onUndo?: () => void }) => {
    if (get(key).phase !== "idle") return;
    if (sessionChanged) { toast.error("The account changed in another tab. Reload this page before saving anything."); return; }
    let n = GRACE_SECONDS;
    set(key, { phase: "queued", left: n, verb });
    const id = toast(`${what} in ${n}s`, {
      duration: Infinity,
      action: { label: "Undo", onClick: () => undo() },
      description: "Nothing is sent until the countdown ends.",
    });
    const tick = setInterval(() => {
      n -= 1;
      if (n <= 0) return;
      set(key, { phase: "queued", left: n, verb });
      toast(`${what} in ${n}s`, { id });
    }, 1000);
    const go = setTimeout(() => {
      clearInterval(tick);
      cancels.delete(key);
      if (!navigator.onLine || !onlineManager.isOnline()) {
        // Offline: send nothing and drop it, rather than leaving it to fire on reconnect after Undo is gone.
        toast.dismiss(id);
        toast.error(`You're offline. ${verb} cancelled and nothing was sent; confirm again once you're back online.`);
        set(key, IDLE);
        onUndo?.();
        return;
      }
      set(key, { phase: "running", left: 0, verb });
      toast.loading(`${what}…`, { id, action: undefined, description: "Keep this tab open until it finishes." });
      // Errors are reported by the caller's mutation; this only releases the lock.
      run().catch(() => {}).finally(() => { toast.dismiss(id); set(key, IDLE); });
    }, GRACE_SECONDS * 1000);
    const undo = (message?: string) => {
      if (get(key).phase !== "queued") return;
      clearInterval(tick);
      clearTimeout(go);
      cancels.delete(key);
      toast.dismiss(id);
      toast(message ?? `${verb} cancelled. Nothing was sent.`);
      set(key, IDLE);
      onUndo?.();
    };
    cancels.set(key, undo);
  };

  const busyLabel = s.phase === "queued" ? `${s.verb} in ${s.left}s` : s.phase === "running" ? `${s.verb}…` : null;
  return {
    phase: s.phase, left: s.left, busy: s.phase !== "idle", running: s.phase === "running", busyLabel, start,
    undo: () => cancels.get(key)?.(),
  };
}

export type Guard = ReturnType<typeof useGuarded>;

export function Spinner() {
  return <Loader2 aria-hidden className="size-4 shrink-0 animate-spin motion-reduce:animate-none" />;
}
