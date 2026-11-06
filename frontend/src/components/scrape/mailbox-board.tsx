"use client";

import { useEffect, useState } from "react";
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

const STATE: Record<MailboxState, { label: string; icon: typeof Check; color: string }> = {
  running: { label: "Scraping", icon: Loader, color: "text-info" },
  failed: { label: "Failed", icon: AlertTriangle, color: "text-destructive" },
  pending: { label: "Waiting", icon: Clock, color: "text-muted-foreground" },
  done: { label: "Scraped", icon: Check, color: "text-success" },
  skipped: { label: "Scraped earlier", icon: SkipForward, color: "text-success" },
};

// The board's sections, in the order a reader cares about them. "Scraped" holds done + skipped.
type Group = "running" | "failed" | "pending" | "finished";
const GROUPS: { key: Group; title: string; short: string; bar: string; dot: string }[] = [
  { key: "finished", title: "Scraped", short: "Scraped", bar: "bg-success", dot: "bg-success" },
  { key: "running", title: "Scraping now", short: "Scraping", bar: "bg-info animate-pulse", dot: "bg-info" },
  { key: "failed", title: "Failed", short: "Failed", bar: "bg-destructive", dot: "bg-destructive" },
  { key: "pending", title: "Waiting to be scraped", short: "Waiting", bar: "bg-muted-foreground/25", dot: "bg-muted-foreground/40" },
];
const LIST_ORDER: Group[] = ["running", "failed", "pending", "finished"];
const groupOf = (s: MailboxState): Group => (s === "done" || s === "skipped" ? "finished" : s);

const n = (x: number) => x.toLocaleString();
function duration(sec: number) {
  if (!isFinite(sec) || sec < 0) return "";
  const s = Math.round(sec);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** Ticks once a second so elapsed time and ETA move between board polls. */
function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

/**
 * Mailboxes of the latest scrape: an overall bar (scraped / scraping / failed / waiting), message totals,
 * and one row per mailbox grouped by state. Clicking a legend item narrows the list; clicking a row
 * filters the log to that mailbox.
 */
export function MailboxBoard({ selected, onSelect }: { selected: string; onSelect: (mailbox: string) => void }) {
  const { data: s } = useQuery(statusQuery);
  const { data: board, error } = useQuery(boardQuery(!!s?.running));
  const [find, setFind] = useState("");
  const [only, setOnly] = useState<Group | null>(null);
  const all = board?.items ?? [];
  const now = useNow(all.some((m) => m.status === "running"));

  const count: Record<Group, number> = { finished: 0, running: 0, failed: 0, pending: 0 };
  for (const m of all) count[groupOf(m.status)]++;
  const total = all.length;
  const pct = total ? Math.round((count.finished / total) * 100) : 0;
  // Message totals are known only once a mailbox has listed its mail, so waiting mailboxes add nothing yet.
  const msgDone = all.reduce((a, m) => a + Math.min(m.fetched, m.total), 0);
  const msgKnown = all.reduce((a, m) => a + m.total, 0);
  const retries = all.reduce((a, m) => a + m.rate_limited, 0);
  // Queue position follows the server's order (running, failed, pending, ...).
  const queue = new Map(all.filter((m) => m.status === "pending").map((m, i) => [m.mailbox, i + 1]));

  const q = find.trim().toLowerCase();
  const shown = all.filter((m) => m.mailbox.includes(q) && (!only || groupOf(m.status) === only));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base font-medium text-muted-foreground">Mailboxes</CardTitle>
        {selected && (
          <CardAction><Button size="sm" variant="ghost" onClick={() => onSelect("")}>All mailboxes</Button></CardAction>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {error && <p role="alert" className="text-sm text-destructive">{error.message}</p>}
        {board && total === 0 && (
          <p className="py-6 text-center text-sm text-muted-foreground">Run a scrape to see each mailbox&apos;s progress here.</p>
        )}

        {total > 0 && (
          <section aria-label="Overall progress" className="flex flex-col gap-2">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3">
              <p className="text-sm"><span className="text-2xl font-semibold tabular-nums">{pct}%</span>
                <span className="ml-2 text-muted-foreground tabular-nums">{count.finished} of {total} mailboxes scraped</span></p>
              {count.running > 0 && <p className="text-xs text-info tabular-nums">{count.running} scraping now</p>}
            </div>
            <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-muted" aria-hidden>
              {GROUPS.map((g) => count[g.key] > 0 && (
                <div key={g.key} className={cn("h-full transition-[width] duration-500", g.bar)} style={{ width: `${(count[g.key] / total) * 100}%` }} />
              ))}
            </div>
            <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4" role="group" aria-label="Show mailboxes by state">
              {GROUPS.map((g) => (
                <button key={g.key} type="button" aria-pressed={only === g.key} disabled={!count[g.key] && only !== g.key}
                  onClick={() => setOnly(only === g.key ? null : g.key)}
                  className={cn("flex flex-col items-start rounded-lg border px-2.5 py-1.5 text-left transition-colors hover:bg-muted/50 disabled:opacity-50 disabled:hover:bg-transparent",
                    "focus-visible:outline-2 focus-visible:outline-ring", only === g.key && "border-primary bg-accent")}>
                  <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <span aria-hidden className={cn("h-2 w-2 shrink-0 rounded-full", g.dot)} />{g.short}
                  </span>
                  <span className="text-lg font-semibold tabular-nums">{count[g.key]}</span>
                </button>
              ))}
            </div>
            {msgKnown > 0 && (
              <p className="text-xs text-muted-foreground tabular-nums">
                {n(msgDone)} of {n(msgKnown)} new messages downloaded so far
                {count.pending > 0 && " · waiting mailboxes are counted once they start"}
                {retries > 0 && <span className="text-warning"> · {n(retries)} rate-limit {retries === 1 ? "retry" : "retries"}</span>}
              </p>
            )}
          </section>
        )}

        {total > 8 && (
          <Input aria-label="Find mailbox" placeholder="Find mailbox" value={find} onChange={(e) => setFind(e.target.value)} />
        )}

        {shown.length > 0 && (
          <ScrollArea className="max-h-[30rem]" viewportProps={{ className: "max-h-[30rem]" }}>
            <div className="flex flex-col gap-3 pr-3">
              {LIST_ORDER.map((g) => {
                const rows = shown.filter((m) => groupOf(m.status) === g);
                if (!rows.length) return null;
                const meta = GROUPS.find((x) => x.key === g)!;
                return (
                  <section key={g} aria-label={meta.title}>
                    <h3 className="mb-1.5 flex items-center gap-1.5 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                      <span aria-hidden className={cn("h-2 w-2 shrink-0 rounded-full", meta.dot)} />{meta.title}
                      <span className="tabular-nums">({rows.length})</span>
                    </h3>
                    <ul className="grid grid-cols-1 gap-1.5">
                      {rows.map((m) => <Row key={m.mailbox} m={m} now={now} queue={queue.get(m.mailbox)} active={m.mailbox === selected}
                        onClick={() => onSelect(m.mailbox === selected ? "" : m.mailbox)} />)}
                    </ul>
                  </section>
                );
              })}
            </div>
          </ScrollArea>
        )}
        {total > 0 && shown.length === 0 && (
          <p className="text-sm text-muted-foreground">No mailbox matches{q ? ` “${find}”` : ""}{only ? ` in ${GROUPS.find((g) => g.key === only)!.title.toLowerCase()}` : ""}.</p>
        )}
      </CardContent>
    </Card>
  );
}

function Row({ m, now, queue, active, onClick }: { m: MailboxProgress; now: number; queue?: number; active: boolean; onClick: () => void }) {
  const st = STATE[m.status];
  const Icon = st.icon;
  const started = m.started_at ? Date.parse(m.started_at) : NaN;
  const finished = m.finished_at ? Date.parse(m.finished_at) : NaN;
  const pct = m.total ? Math.min(100, Math.round((m.fetched / m.total) * 100)) : 0;

  let detail: string;
  let sub: string | null = null;
  if (m.status === "running") {
    const elapsed = (now - started) / 1000;
    const rate = elapsed > 5 && m.fetched > 0 ? m.fetched / elapsed : 0;
    detail = m.total ? `${pct}%` : "Listing mail…";
    sub = [m.total ? `${n(m.fetched)} of ${n(m.total)} messages` : null,
      isFinite(elapsed) ? `${duration(elapsed)} elapsed` : null,
      rate ? `${rate.toFixed(1)}/s` : null,
      rate && m.total > m.fetched ? `about ${duration((m.total - m.fetched) / rate)} left` : null,
    ].filter(Boolean).join(" · ") || null;
  } else if (m.status === "done") {
    detail = m.total ? `${n(m.total)} new` : "No new mail";
    const took = (finished - started) / 1000;
    sub = isFinite(took) ? `took ${duration(took)}` : null;
  } else if (m.status === "pending") {
    detail = queue ? `#${queue} in queue` : st.label;
  } else if (m.status === "skipped") {
    detail = "Earlier run";
  } else {
    detail = m.total ? `stopped at ${pct}%` : "Error";
    sub = m.error ? m.error.split("\n")[0] : null;
  }

  const row = (
    <button type="button" onClick={onClick} aria-pressed={active}
      aria-label={`${m.mailbox}: ${st.label}. ${detail}${sub ? `, ${sub}` : ""}. Show its log lines.`}
      className={cn("flex w-full min-w-0 flex-col gap-1.5 rounded-lg border px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted/50",
        "focus-visible:outline-2 focus-visible:outline-ring", active && "border-primary bg-accent",
        m.status === "running" && !active && "border-info/40 bg-info/5",
        m.status === "failed" && "border-destructive/30 bg-destructive/5",
        m.status === "pending" && "text-muted-foreground")}>
      <span className="flex min-w-0 items-center gap-2">
        <Icon aria-hidden className={cn("h-4 w-4 shrink-0", st.color, m.status === "running" && "animate-spin")} />
        <span className={cn("truncate", m.status !== "pending" && "text-foreground")} title={m.mailbox}>{m.mailbox}</span>
        <span className={cn("ml-auto shrink-0 text-xs tabular-nums", m.status === "running" ? "font-medium text-info" : "text-muted-foreground")}>{detail}</span>
      </span>
      {m.status === "running" && <Progress value={pct} aria-hidden className={cn("h-1.5", !m.total && "animate-pulse")} />}
      {sub && <span className={cn("text-xs break-words tabular-nums", m.status === "failed" ? "text-destructive" : "text-muted-foreground")}>{sub}</span>}
      {m.rate_limited > 0 && <span className="text-xs text-warning tabular-nums">{n(m.rate_limited)} rate-limit {m.rate_limited === 1 ? "retry" : "retries"}</span>}
    </button>
  );
  return (
    <li className="min-w-0">
      {m.status === "failed" && m.error ? (
        <Tooltip>
          <TooltipTrigger render={row} />
          <TooltipContent className="max-w-sm break-words">{m.error}</TooltipContent>
        </Tooltip>
      ) : row}
    </li>
  );
}
