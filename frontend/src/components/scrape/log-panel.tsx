"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowDown, Maximize2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Switch } from "@/components/ui/switch";
import { boardQuery, logQuery, statusQuery } from "@/lib/api";
import { cn } from "@/lib/utils";

// Case-sensitive on purpose: progress lines like "extract: … 0 failed" are not problems.
const PROBLEM = /FAILED|ERROR|rate-limit|failed extraction|connection lost|[1-9]\d* failed$/;
// Within this many pixels of the bottom counts as "at the bottom" (keeps following).
const BOTTOM_SLACK = 24;

/** Job log. The board's mailbox filter narrows it; the latest job's log is shown otherwise. */
export function LogPanel({ mailbox, onClearMailbox }: { mailbox: string; onClearMailbox: () => void }) {
  const { data: s } = useQuery(statusQuery);
  const running = !!s?.running;
  const { data: board } = useQuery(boardQuery(running));
  // A mailbox filter belongs to the board's scrape job; otherwise follow the latest job of any kind.
  const jobId = mailbox ? board?.job_id ?? null : s?.job_id ?? null;
  const { data, error } = useQuery(logQuery(jobId, mailbox, running));
  const lines = data?.lines ?? [];
  const [expanded, setExpanded] = useState(false);

  return (
    <Card className="min-w-0 xl:sticky xl:top-20 xl:self-start">
      <CardHeader>
        <CardTitle className="flex min-w-0 flex-wrap items-center gap-2 text-base font-medium text-muted-foreground">
          Log
          {mailbox && (
            <span className="inline-flex min-w-0 items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary-text">
              <span className="truncate">{mailbox}</span>
              <button type="button" aria-label="Show all mailboxes" onClick={onClearMailbox} className="rounded-full hover:bg-primary/20">
                <X className="h-3 w-3 shrink-0" />
              </button>
            </span>
          )}
        </CardTitle>
        <CardAction>
          <Button size="sm" variant="ghost" onClick={() => setExpanded(true)} aria-label="Expand log to full screen">
            <Maximize2 className="h-4 w-4 shrink-0" /> Expand
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        <LogView lines={lines} error={error?.message} empty={jobId == null ? "No job has run yet." : "No log lines yet."}
          heightClass="h-[60vh] min-h-[20rem] xl:h-[calc(100vh-12rem)]" />
      </CardContent>

      <Dialog open={expanded} onOpenChange={setExpanded}>
        <DialogContent className="flex h-[92vh] w-[96vw] max-w-[96vw] flex-col sm:max-w-[96vw]">
          <DialogHeader><DialogTitle>Log{mailbox ? ` · ${mailbox}` : ""}</DialogTitle></DialogHeader>
          <LogView lines={lines} error={error?.message} empty="No log lines yet." heightClass="min-h-0 flex-1" />
        </DialogContent>
      </Dialog>
    </Card>
  );
}

/**
 * Follows new lines while the reader is at the bottom. Scrolling up pauses following;
 * scrolling back to the bottom (or the switch, or "Jump to latest") turns it on again.
 */
function LogView({ lines, error, empty, heightClass }: { lines: string[]; error?: string; empty: string; heightClass: string }) {
  const viewport = useRef<HTMLDivElement>(null);
  const [follow, setFollow] = useState(true);

  const toBottom = () => {
    const el = viewport.current;
    if (el) el.scrollTop = el.scrollHeight;
  };
  // New lines (or turning follow back on) jump to the bottom before paint, so there is no flicker.
  useLayoutEffect(() => { if (follow) toBottom(); }, [lines, follow]);
  useEffect(() => { toBottom(); }, []);

  const onScroll = () => {
    const el = viewport.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_SLACK;
    if (atBottom !== follow) setFollow(atBottom);
  };

  return (
    <div className={cn("relative flex flex-col gap-2", heightClass)}>
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <Label className="gap-2 font-normal">
          <Switch checked={follow} onCheckedChange={(on) => setFollow(!!on)} aria-label="Auto-scroll to new lines" />
          Auto-scroll {follow ? "on" : "paused"}
        </Label>
        <span className="tabular-nums">{lines.length.toLocaleString()} lines</span>
        {error && <span role="alert" className="text-destructive">{error}</span>}
      </div>
      <ScrollArea className="min-h-0 flex-1 rounded-lg bg-sidebar ring-1 ring-border" viewportRef={viewport} onViewportScroll={onScroll}
        viewportProps={{ tabIndex: 0, "aria-label": "Job log", role: "log" } as React.HTMLAttributes<HTMLDivElement>}>
        <pre className="p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words text-foreground">
          {lines.length ? lines.map((line, i) => (
            <span key={i} className={cn("block", PROBLEM.test(line) && "-mx-3 bg-destructive/20 px-3 font-medium")}>{line}</span>
          )) : <span className="text-muted-foreground">{empty}</span>}
        </pre>
      </ScrollArea>
      {!follow && lines.length > 0 && (
        <Button size="sm" className="absolute right-4 bottom-4 shadow-md" onClick={() => setFollow(true)}>
          <ArrowDown className="h-4 w-4 shrink-0" /> Jump to latest
        </Button>
      )}
    </div>
  );
}
