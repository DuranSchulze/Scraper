"use client";

import { useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { CalendarClock, CheckCircle2, ChevronLeft, ChevronRight, Download, FileSpreadsheet, FileText, Inbox, Printer, Timer, UserPlus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { PageHeader } from "@/components/shell/page-header";
import { Pill } from "@/components/review/badges";
import { DateRangeFilter } from "@/components/review/date-range";
import { LOG_PAGE_SIZE, logsDailyQuery, logsExportUrl, logsJobsQuery, logsPublishedQuery, logsSummaryQuery } from "@/lib/api";
import { day, phDateTime, stamp } from "@/lib/format";
import type { JobStatus, LogJob, LogRange } from "@/lib/types";
import { cn } from "@/lib/utils";

const DASH = <span className="text-muted-foreground">—</span>;
const STATUS: Record<JobStatus, { label: string; tone: "neutral" | "info" | "warning" | "success" | "danger" }> = {
  running: { label: "Running", tone: "info" }, paused: { label: "Paused", tone: "warning" },
  done: { label: "Done", tone: "success" }, done_with_errors: { label: "Done with errors", tone: "warning" },
  failed: { label: "Failed", tone: "danger" }, interrupted: { label: "Interrupted", tone: "danger" }, stopped: { label: "Stopped", tone: "neutral" },
};
const n = (v?: number | null) => (v ?? 0).toLocaleString("en-US");

/** Today's Philippine date, and the date `days` before it. */
function defaultRange(): LogRange {
  const to = day(new Date().toISOString());
  const from = day(new Date(Date.parse(`${to}T00:00:00+08:00`) - 29 * 86_400_000).toISOString());
  return { from, to };
}

function duration(seconds: number | null) {
  if (seconds == null) return "";
  const m = Math.round(seconds / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

export function LogsView() {
  const router = useRouter(), pathname = usePathname(), params = useSearchParams();
  const fallback = defaultRange();
  const range: LogRange = { from: params.get("from") || fallback.from, to: params.get("to") || fallback.to };
  const setRange = (from: string, to: string) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries({ from, to })) if (v) next.set(k, v); else next.delete(k);
    router.replace(`${pathname}?${next}`, { scroll: false });
  };

  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Logs" description="Scrape history, records added to the client directory and totals, in Philippine time."
        actions={
          <div data-print="hide" className="flex flex-wrap items-center gap-2">
            <DateRangeFilter from={range.from} to={range.to} onChange={setRange}
              className="h-8 rounded-full border-transparent bg-muted/40 font-normal" />
            <ExportMenu range={range} />
          </div>
        } />
      <p className="hidden text-sm print:block">Report for {range.from} to {range.to}, Philippine time.</p>
      <SummaryTiles range={range} />
      <JobsTable range={range} />
      <div className="grid gap-5 xl:grid-cols-2">
        <DailyTable range={range} />
        <PublishedTable range={range} />
      </div>
    </div>
  );
}

function ExportMenu({ range }: { range: LogRange }) {
  const item = "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring";
  // Print in the light theme so the PDF is readable on paper, then restore the viewer's theme.
  const print = () => {
    const html = document.documentElement, prev = html.getAttribute("data-theme");
    html.setAttribute("data-theme", "light");
    const restore = () => { if (prev) html.setAttribute("data-theme", prev); else html.removeAttribute("data-theme"); };
    window.addEventListener("afterprint", restore, { once: true });
    window.print();
  };
  return (
    <Popover>
      <PopoverTrigger render={<Button size="sm" className="h-8 rounded-full transition-transform hover:-translate-y-0.5 active:scale-[0.97] motion-reduce:transform-none" />}>
        <Download aria-hidden className="h-4 w-4 shrink-0" /> Export
      </PopoverTrigger>
      <PopoverContent align="end" className="w-64 gap-1 p-1.5">
        <p className="px-2 pt-1 text-xs font-medium text-muted-foreground">Excel</p>
        <a className={item} href={logsExportUrl(range, "all", "xlsx")} download>
          <FileSpreadsheet aria-hidden className="h-4 w-4 shrink-0 text-success" /> Full report (.xlsx)
        </a>
        <p className="px-2 pt-2 text-xs font-medium text-muted-foreground">CSV</p>
        {([["jobs", "Scrape runs"], ["daily", "Daily totals"], ["published", "Added to directory"]] as const).map(([t, label]) => (
          <a key={t} className={item} href={logsExportUrl(range, t, "csv")} download>
            <FileText aria-hidden className="h-4 w-4 shrink-0 text-muted-foreground" /> {label} (.csv)
          </a>
        ))}
        <p className="px-2 pt-2 text-xs font-medium text-muted-foreground">PDF</p>
        <button type="button" className={item} onClick={print}>
          <Printer aria-hidden className="h-4 w-4 shrink-0 text-muted-foreground" /> Print or save as PDF
        </button>
      </PopoverContent>
    </Popover>
  );
}

function Tile({ icon: Icon, label, value, sub }: { icon: typeof Inbox; label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-xl bg-card p-4">
      <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <Icon aria-hidden className="h-3.5 w-3.5 shrink-0" />{label}
      </span>
      <span className="text-lg leading-snug font-semibold tabular-nums break-words">{value}</span>
      {sub && <span className="text-xs text-muted-foreground">{sub}</span>}
    </div>
  );
}

function SummaryTiles({ range }: { range: LogRange }) {
  const { data: s, error } = useQuery(logsSummaryQuery(range));
  if (error) return <p role="alert" className="text-sm text-destructive">{error.message}</p>;
  if (!s) return <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-24 rounded-xl" />)}</div>;
  const next = !s.schedule.enabled ? "Auto-scrape off" : s.next_run_at ? phDateTime(s.next_run_at) : "After the current job";
  return (
    <section aria-label="Summary" className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
      <Tile icon={CheckCircle2} label="Last complete scrape" value={s.last_complete_scrape_at ? stamp(s.last_complete_scrape_at) : "None yet"}
        sub={s.last_full_scrape_at ? `Last full rescan ${stamp(s.last_full_scrape_at)}` : "No full rescan yet"} />
      <Tile icon={CalendarClock} label="Next automatic run" value={next}
        sub={s.schedule.enabled && s.schedule.interval_minutes ? `Every ${s.schedule.interval_minutes} min` : undefined} />
      <Tile icon={Inbox} label="Emails scraped" value={n(s.fetched.gmail)}
        sub={`${n(s.fetched.contacts)} contacts · ${n(s.stored.gmail)} stored in total`} />
      <Tile icon={UserPlus} label="Added to directory" value={n(s.published)} sub={`${n(s.clients_total)} clients in the directory`} />
      <Tile icon={Timer} label="Scrape runs" value={n(s.runs.total)}
        sub={`${n(s.runs.completed)} completed · ${n(s.runs.auto)} automatic · ${n(s.runs.full)} full`} />
      <Tile icon={FileSpreadsheet} label="Waiting for extraction" value={n(s.backlog_pending)}
        sub={`${n(s.candidates.pending)} records pending review`} />
    </section>
  );
}

function Section({ title, count, children, footer }: { title: string; count?: number; children: React.ReactNode; footer?: React.ReactNode }) {
  return (
    <Card className="gap-0 overflow-hidden py-0">
      <div className="flex items-baseline gap-2 px-4 py-3">
        <h2 className="text-base font-medium text-muted-foreground">{title}</h2>
        {count != null && <span className="text-sm font-semibold text-primary-text tabular-nums">{n(count)}</span>}
      </div>
      {children}
      {footer}
    </Card>
  );
}

function Pager({ page, total, onPage }: { page: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / LOG_PAGE_SIZE));
  if (pages < 2) return null;
  return (
    <div data-print="hide" className="flex items-center justify-between gap-3 bg-muted/30 px-4 py-2 text-sm text-muted-foreground">
      <span className="tabular-nums">Page {n(page)} of {n(pages)}</span>
      <span className="flex gap-1">
        <Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page"><ChevronLeft className="h-4 w-4" /></Button>
        <Button size="sm" variant="ghost" disabled={page >= pages} onClick={() => onPage(page + 1)} aria-label="Next page"><ChevronRight className="h-4 w-4" /></Button>
      </span>
    </div>
  );
}

function Rows({ cols, loading, error, empty, children }: { cols: number; loading: boolean; error: Error | null; empty: boolean; children: React.ReactNode }) {
  if (error) return <TableRow><TableCell colSpan={cols} className="py-8 text-center text-destructive">{error.message}</TableCell></TableRow>;
  if (loading) return <>{Array.from({ length: 4 }, (_, i) => <TableRow key={i}><TableCell colSpan={cols}><Skeleton className="h-4 w-full" /></TableCell></TableRow>)}</>;
  if (empty) return <TableRow><TableCell colSpan={cols} className="py-8 text-center text-muted-foreground">Nothing in this date range.</TableCell></TableRow>;
  return <>{children}</>;
}

const HEAD = "bg-muted [&_th]:h-9 [&_th]:px-3 [&_th]:text-xs [&_th]:font-medium [&_th]:text-muted-foreground";

function JobsTable({ range }: { range: LogRange }) {
  const [state, setState] = useState({ key: "", page: 1 });
  const key = `${range.from}|${range.to}`;
  const page = state.key === key ? state.page : 1;
  const { data, error, isPending } = useQuery(logsJobsQuery(range, page));
  return (
    <Section title="Scrape runs" count={data?.total}
      footer={data && <Pager page={page} total={data.total} onPage={(p) => setState({ key, page: p })} />}>
      <Table className="min-w-[960px]" containerClassName="overflow-auto">
        <TableHeader className={HEAD}>
          <TableRow className="hover:bg-transparent">
            <TableHead className="pl-4">Started (PHT)</TableHead><TableHead>Run</TableHead><TableHead>Status</TableHead>
            <TableHead>Duration</TableHead><TableHead className="text-right">Mailboxes</TableHead>
            <TableHead className="text-right">Emails fetched</TableHead><TableHead className="pr-4">Notes</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody className="[&_td]:px-3 [&_td]:py-2.5">
          <Rows cols={7} loading={isPending} error={error} empty={!!data && !data.rows.length}>
            {data?.rows.map((j) => <JobRow key={j.id} j={j} />)}
          </Rows>
        </TableBody>
      </Table>
    </Section>
  );
}

function JobRow({ j }: { j: LogJob }) {
  const status = STATUS[j.status] ?? { label: j.status, tone: "neutral" as const };
  return (
    <TableRow className="align-top">
      <TableCell className="pl-4 text-sm tabular-nums">{stamp(j.started_at)}</TableCell>
      <TableCell>
        <span className="flex flex-wrap items-center gap-1">
          <span className="text-sm font-medium capitalize">{j.name}</span>
          <Pill tone="neutral">{j.auto ? "Auto" : "Manual"}</Pill>
          {j.full && <Pill tone="info">Full rescan</Pill>}
          <span className="text-xs text-muted-foreground tabular-nums">#{j.id}</span>
        </span>
      </TableCell>
      <TableCell>
        <span className="flex flex-wrap items-center gap-1">
          <Pill tone={status.tone}>{status.label}</Pill>
          {/* "Done" already means complete; flag it only when a run finished with errors yet still counts as complete. */}
          {j.completed && j.status !== "done" && <Pill tone="success">Counts as complete</Pill>}
        </span>
      </TableCell>
      <TableCell className="text-sm tabular-nums">{duration(j.seconds) || DASH}</TableCell>
      <TableCell className="text-right text-sm tabular-nums">{j.mailboxes ? `${n(j.mailboxes_done)}/${n(j.mailboxes)}` : DASH}</TableCell>
      <TableCell className="text-right text-sm tabular-nums">{n(j.emails_fetched)}</TableCell>
      <TableCell className="max-w-72 pr-4 text-xs text-muted-foreground">
        {j.failures ? `${n(j.failures)} failure${j.failures === 1 ? "" : "s"}. ` : ""}
        <span className="break-words">{j.error ?? ""}</span>
      </TableCell>
    </TableRow>
  );
}

function DailyTable({ range }: { range: LogRange }) {
  const { data, error, isPending } = useQuery(logsDailyQuery(range));
  return (
    <Section title="Daily totals">
      <Table containerClassName="max-h-[28rem] overflow-auto print:max-h-none">
        <TableHeader className={cn(HEAD, "sticky top-0 z-10")}>
          <TableRow className="hover:bg-transparent">
            <TableHead className="pl-4">Date (PHT)</TableHead><TableHead className="text-right">Emails</TableHead>
            <TableHead className="text-right">Contacts</TableHead><TableHead className="text-right">Runs</TableHead>
            <TableHead className="pr-4 text-right">Added</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody className="[&_td]:px-3 [&_td]:py-2 text-sm tabular-nums">
          <Rows cols={5} loading={isPending} error={error} empty={!!data && !data.length}>
            {data?.map((d) => (
              <TableRow key={d.day} className={cn(!d.emails && !d.runs && !d.published && "text-muted-foreground")}>
                <TableCell className="pl-4">{d.day}</TableCell>
                <TableCell className="text-right">{n(d.emails)}</TableCell>
                <TableCell className="text-right">{n(d.contacts)}</TableCell>
                <TableCell className="text-right">{n(d.runs)}{d.runs ? <span className="text-muted-foreground"> ({n(d.completed_runs)} done)</span> : ""}</TableCell>
                <TableCell className="pr-4 text-right">{n(d.published)}</TableCell>
              </TableRow>
            ))}
          </Rows>
        </TableBody>
      </Table>
    </Section>
  );
}

function PublishedTable({ range }: { range: LogRange }) {
  const [state, setState] = useState({ key: "", page: 1 });
  const key = `${range.from}|${range.to}`;
  const page = state.key === key ? state.page : 1;
  const { data, error, isPending } = useQuery(logsPublishedQuery(range, page));
  return (
    <Section title="Added to directory" count={data?.total}
      footer={data && <Pager page={page} total={data.total} onPage={(p) => setState({ key, page: p })} />}>
      <Table containerClassName="overflow-auto">
        <TableHeader className={HEAD}>
          <TableRow className="hover:bg-transparent">
            <TableHead className="pl-4">Client</TableHead><TableHead>First added (PHT)</TableHead><TableHead className="pr-4">Approved by</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody className="[&_td]:px-3 [&_td]:py-2.5">
          <Rows cols={3} loading={isPending} error={error} empty={!!data && !data.rows.length}>
            {data?.rows.map((p) => (
              <TableRow key={p.id} className="align-top">
                <TableCell className="max-w-64 pl-4">
                  <span className="block truncate text-sm font-medium">{p.name || "Unnamed"}</span>
                  <span className="block truncate text-xs text-muted-foreground">{p.email}{p.company ? ` · ${p.company}` : ""}</span>
                </TableCell>
                <TableCell className="text-sm tabular-nums">{stamp(p.first_published_at)}</TableCell>
                <TableCell className="pr-4 text-sm">{p.reviewed_by || DASH}</TableCell>
              </TableRow>
            ))}
          </Rows>
        </TableBody>
      </Table>
    </Section>
  );
}
