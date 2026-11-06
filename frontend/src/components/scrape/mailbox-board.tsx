"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Check, Clock, Loader, SkipForward } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { boardQuery, statusQuery } from "@/lib/api";
import type { MailboxProgress, MailboxState } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Pill } from "@/components/review/badges";

const STATE: Record<MailboxState, { label: string; tone: "neutral" | "info" | "success" | "danger" | "warning"; icon: typeof Check }> = {
  running: { label: "Running", tone: "info", icon: Loader },
  failed: { label: "Failed", tone: "danger", icon: AlertTriangle },
  pending: { label: "Waiting", tone: "neutral", icon: Clock },
  done: { label: "Done", tone: "success", icon: Check },
  skipped: { label: "Done earlier", tone: "success", icon: SkipForward },
};
const ORDER: MailboxState[] = ["running", "failed", "pending", "done", "skipped"];

/** One chip per mailbox of the latest scrape; clicking a chip filters the log to that mailbox. */
export function MailboxBoard({ selected, onSelect }: { selected: string; onSelect: (mailbox: string) => void }) {
  const { data: s } = useQuery(statusQuery);
  const { data: board, error } = useQuery(boardQuery(!!s?.running));
  const [find, setFind] = useState("");
  const items = (board?.items ?? []).filter((m) => m.mailbox.includes(find.trim().toLowerCase()));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base font-medium text-muted-foreground">Mailboxes</CardTitle>
        {selected && (
          <CardAction><Button size="sm" variant="ghost" onClick={() => onSelect("")}>All mailboxes</Button></CardAction>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {board && board.items.length > 0 && (
          <div className="flex flex-wrap gap-1.5" aria-label="Mailbox counts">
            {ORDER.filter((k) => board.counts[k]).map((k) => (
              <Pill key={k} tone={STATE[k].tone}>{board.counts[k]} {STATE[k].label.toLowerCase()}</Pill>
            ))}
          </div>
        )}
        {board && board.items.length > 8 && (
          <Input aria-label="Find mailbox" placeholder="Find mailbox" value={find} onChange={(e) => setFind(e.target.value)} />
        )}
        {error && <p role="alert" className="text-sm text-destructive">{error.message}</p>}
        {board && board.items.length === 0 && (
          <p className="py-6 text-center text-sm text-muted-foreground">Run a scrape to see each mailbox&apos;s progress here.</p>
        )}
        {items.length > 0 && (
          <ScrollArea className="max-h-[26rem]" viewportProps={{ className: "max-h-[26rem]" }}>
            <ul className="grid grid-cols-1 gap-1.5 pr-3">
              {items.map((m) => <Chip key={m.mailbox} m={m} active={m.mailbox === selected}
                onClick={() => onSelect(m.mailbox === selected ? "" : m.mailbox)} />)}
            </ul>
          </ScrollArea>
        )}
        {board && board.items.length > 0 && items.length === 0 && (
          <p className="text-sm text-muted-foreground">No mailbox matches “{find}”.</p>
        )}
      </CardContent>
    </Card>
  );
}

function Chip({ m, active, onClick }: { m: MailboxProgress; active: boolean; onClick: () => void }) {
  const st = STATE[m.status];
  const Icon = st.icon;
  const pct = m.total ? Math.round((m.fetched / m.total) * 100) : 0;
  const detail = m.status === "running" ? (m.total ? `${m.fetched.toLocaleString()}/${m.total.toLocaleString()}` : "listing…")
    : m.status === "done" && m.total ? `${m.total.toLocaleString()} new` : st.label;
  const chip = (
    <button type="button" onClick={onClick} aria-pressed={active}
      aria-label={`${m.mailbox}: ${st.label}${m.status === "running" && m.total ? `, ${m.fetched} of ${m.total}` : ""}. Show its log lines.`}
      className={cn("flex w-full min-w-0 flex-col gap-1 rounded-lg border px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted/50",
        "focus-visible:outline-2 focus-visible:outline-ring", active && "border-primary bg-accent",
        m.status === "failed" && "border-destructive/30 bg-destructive/5")}>
      <span className="flex min-w-0 items-center gap-2">
        <Icon aria-hidden className={cn("h-4 w-4 shrink-0", m.status === "running" && "animate-spin text-info",
          m.status === "failed" && "text-destructive", (m.status === "done" || m.status === "skipped") && "text-success",
          m.status === "pending" && "text-muted-foreground")} />
        <span className="truncate" title={m.mailbox}>{m.mailbox}</span>
        <span className="ml-auto shrink-0 text-xs text-muted-foreground tabular-nums">{detail}</span>
      </span>
      {m.status === "running" && m.total > 0 && <Progress value={pct} aria-hidden className="h-1" />}
      {m.rate_limited > 0 && <span className="text-xs text-warning tabular-nums">{m.rate_limited} rate-limit retries</span>}
    </button>
  );
  return (
    <li className="min-w-0">
      {m.status === "failed" && m.error ? (
        <Tooltip>
          <TooltipTrigger render={chip} />
          <TooltipContent className="max-w-sm break-words">{m.error}</TooltipContent>
        </Tooltip>
      ) : chip}
    </li>
  );
}
