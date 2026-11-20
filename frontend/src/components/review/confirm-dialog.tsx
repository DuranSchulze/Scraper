"use client";

import { useEffect, useRef, useState } from "react";
import { Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Spinner, type Guard } from "@/lib/guarded";

type Copy = { title: string; description: string; confirmLabel: string };

/**
 * While `pending`, or while the `guard` write this dialog confirmed is queued or running, the dialog stays open and
 * cannot be closed (Cancel, Escape, outside click) and confirm cannot fire twice. During the countdown Undo sits
 * here, inside the active modal, so keyboard and screen-reader users reach it.
 */
export function ConfirmDialog({ open, onOpenChange, title, description, confirmLabel, onConfirm, pending, guard }: Copy & {
  open: boolean; onOpenChange: (open: boolean) => void; onConfirm: () => void; pending?: boolean; guard?: Guard;
}) {
  // Focus lands on confirm, so a keyboard user confirms with Enter; Undo covers a slip.
  const confirmRef = useRef<HTMLButtonElement>(null);
  const undoRef = useRef<HTMLButtonElement>(null);
  // The copy shown when confirmed, kept while the write is queued or running (the caller's counts may change).
  const [confirmed, setConfirmed] = useState<Copy | null>(null);
  if (confirmed && guard && !guard.busy) setConfirmed(null);
  const tracking = !!confirmed && !!guard?.busy;
  const locked = !!pending || tracking;
  const copy = tracking ? confirmed : { title, description, confirmLabel };
  const queued = tracking && guard?.phase === "queued";

  // Confirm turns disabled once clicked; move focus to Undo so it isn't lost.
  useEffect(() => { if (queued) undoRef.current?.focus(); }, [queued]);

  return (
    // A write under this guard that another dialog instance confirmed (e.g. before a remount) blocks a new confirm.
    <Dialog open={tracking || (open && !guard?.busy)} onOpenChange={(o) => { if (!locked) onOpenChange(o); }}>
      <DialogContent initialFocus={confirmRef} showCloseButton={!locked}>
        <DialogHeader>
          <DialogTitle>{copy.title}</DialogTitle>
          <DialogDescription>{copy.description}</DialogDescription>
        </DialogHeader>
        {tracking && (
          <p role="status" className="text-xs text-muted-foreground">
            {queued ? `Nothing is sent for ${guard!.left} more second${guard!.left === 1 ? "" : "s"}. Undo cancels it.`
              : "Sending now. This can't be stopped; keep this tab open until it finishes."}
          </p>
        )}
        <DialogFooter>
          {queued
            ? <Button ref={undoRef} variant="outline" onClick={() => guard!.undo()}><Undo2 aria-hidden className="size-4 shrink-0" />Undo</Button>
            : <Button variant="outline" onClick={() => onOpenChange(false)} disabled={locked}>Cancel</Button>}
          <Button ref={confirmRef} disabled={locked} aria-busy={locked}
            onClick={() => {
              if (locked) return;
              if (guard) setConfirmed({ title, description, confirmLabel });
              onConfirm();
            }}>
            {tracking ? <><Spinner />{guard!.busyLabel}</> : pending ? <><Spinner />Working…</> : copy.confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
