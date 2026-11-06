"use client";

import { useQuery } from "@tanstack/react-query";
import { BookUser, ChevronRight, FileSpreadsheet, Mail } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { candidatesQuery, type Filters } from "@/lib/api";
import { day, money } from "@/lib/format";
import type { CandidateRow } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Pill, StageBadge } from "./badges";

const DASH = <span className="text-muted-foreground">—</span>;
// The pinned Client cell needs an opaque background, or scrolled cells show through it.
const PINNED = "sticky left-0 z-10 bg-card group-hover:bg-[color-mix(in_oklab,var(--muted)_40%,var(--card))] group-aria-selected:bg-accent";
const SOURCES = [
  { key: "gmail", label: "emails", icon: Mail },
  { key: "contacts", label: "contacts", icon: BookUser },
  { key: "sheets", label: "sheet rows", icon: FileSpreadsheet },
] as const;

export function RecordsTable({ filters, selectedId, onSelect, toolbar }: {
  filters: Filters; selectedId: number | null; onSelect: (id: number) => void; toolbar?: React.ReactNode;
}) {
  const { data, error, isPending, isFetching } = useQuery(candidatesQuery(filters));
  const rows = data?.rows;
  return (
    <Card className="gap-0 overflow-hidden py-0">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/60 px-4 py-3">
        <div className="flex items-baseline gap-2">
          <h2 className="text-base font-medium text-muted-foreground">Records</h2>
          <span className="text-sm font-semibold text-primary tabular-nums" aria-live="polite">
            {data ? data.total.toLocaleString() : "…"}
          </span>
          {data && data.total > data.rows.length && (
            <span className="text-xs text-muted-foreground tabular-nums">showing the latest {data.rows.length.toLocaleString()}</span>
          )}
        </div>
        {toolbar}
      </div>
      <Table className="min-w-[1180px]" containerClassName="max-h-[calc(100vh-15rem)] min-h-[16rem] overflow-auto">
          <TableHeader className="sticky top-0 z-20 bg-muted [&_th]:h-9 [&_th]:px-3 [&_th]:text-xs [&_th]:font-medium [&_th]:text-muted-foreground">
            <TableRow className="hover:bg-transparent">
              <TableHead className="sticky left-0 z-10 w-72 bg-muted pl-4">Client</TableHead>
              <TableHead className="w-36">Phone</TableHead>
              <TableHead className="w-52">Services</TableHead>
              <TableHead className="w-28">Stage</TableHead>
              <TableHead className="w-24">Status</TableHead>
              <TableHead className="w-36 text-right">Latest amount</TableHead>
              <TableHead className="w-20">Paid</TableHead>
              <TableHead className="w-40" title="Scraped emails, contacts and sheet rows behind this record">Scraped</TableHead>
              <TableHead className="w-28">First contact</TableHead>
              <TableHead className="w-28">Last contact</TableHead>
              <TableHead className="w-24">Checks</TableHead>
              <TableHead className="w-8 pr-4"><span className="sr-only">Open</span></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody className={cn("transition-opacity [&_td]:px-3 [&_td]:py-2.5", (isPending || isFetching) && !rows && "opacity-60")}>
            {error && <TableRow><TableCell colSpan={12} className="py-10 text-center text-destructive">{error.message}</TableCell></TableRow>}
            {isPending && !error && <TableRow><TableCell colSpan={12} className="py-10 text-center text-muted-foreground">Loading records…</TableCell></TableRow>}
            {rows?.length === 0 && <TableRow><TableCell colSpan={12} className="py-10 text-center text-muted-foreground">No records match these filters.</TableCell></TableRow>}
            {rows?.map((r) => <Row key={r.id} r={r} selected={r.id === selectedId} onSelect={onSelect} />)}
          </TableBody>
      </Table>
    </Card>
  );
}

function Row({ r, selected, onSelect }: { r: CandidateRow; selected: boolean; onSelect: (id: number) => void }) {
  const more = r.services.slice(2);
  return (
    <TableRow tabIndex={0} aria-selected={selected} aria-label={`Open record ${r.id}: ${r.name || r.email_key}`}
      onClick={() => onSelect(r.id)} onKeyDown={(e) => e.key === "Enter" && onSelect(r.id)}
      className={cn("group cursor-pointer border-border/50 align-top hover:bg-muted/40 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring",
        selected && "bg-accent hover:bg-accent")}>
      <TableCell className={cn(PINNED, "max-w-72 pl-4")}>
        <div className="flex min-w-0 flex-col">
          <span className="truncate font-medium" title={r.name || undefined}>{r.name || "Unnamed"}</span>
          <span className="truncate text-xs text-muted-foreground" title={r.email_key}>{r.email_key}</span>
          {r.company && <span className="truncate text-xs text-muted-foreground" title={r.company}>{r.company}</span>}
        </div>
      </TableCell>
      <TableCell className="text-sm tabular-nums">
        {r.phones.length ? <span title={r.phones.join(", ")}>{r.phones[0]}{r.phones.length > 1 && <span className="text-muted-foreground"> +{r.phones.length - 1}</span>}</span> : DASH}
      </TableCell>
      <TableCell>
        {r.services.length ? (
          <div className="flex flex-wrap gap-1">
            {r.services.slice(0, 2).map((s) => <Pill key={s} className="max-w-40 truncate">{s}</Pill>)}
            {more.length > 0 && <Pill tone="info" className="cursor-help"><span title={more.join(", ")}>+{more.length}</span></Pill>}
          </div>
        ) : DASH}
      </TableCell>
      <TableCell><StageBadge stage={r.stage} /></TableCell>
      <TableCell className="text-sm capitalize">{r.status || DASH}</TableCell>
      <TableCell className="text-right">
        {r.latest_amount ? (
          <div className="flex flex-col items-end">
            <span className="font-medium tabular-nums">{money(r.latest_amount)}</span>
            <span className="text-xs text-muted-foreground capitalize">{r.latest_amount.stage}{r.latest_amount.date ? ` · ${day(r.latest_amount.date)}` : ""}</span>
          </div>
        ) : DASH}
      </TableCell>
      <TableCell>{r.paid ? <Pill tone="success">Paid</Pill> : <span className="text-xs text-muted-foreground">No</span>}</TableCell>
      <TableCell>
        <div className="flex flex-col gap-1">
          <span className="font-medium tabular-nums">{r.items.toLocaleString()} <span className="text-xs font-normal text-muted-foreground">{r.items === 1 ? "item" : "items"}</span></span>
          <span className="flex flex-wrap gap-x-2.5 text-xs text-muted-foreground tabular-nums">
            {SOURCES.filter((s) => r.item_sources[s.key]).map(({ key, label, icon: Icon }) => (
              <span key={key} className="inline-flex items-center gap-1" title={`${r.item_sources[key].toLocaleString()} ${label}`}>
                <Icon aria-hidden className="h-3.5 w-3.5 shrink-0" />{r.item_sources[key].toLocaleString()}
                <span className="sr-only">{label}</span>
              </span>
            ))}
          </span>
        </div>
      </TableCell>
      <TableCell className="text-sm tabular-nums">{day(r.first_contact) || DASH}</TableCell>
      <TableCell className="text-sm tabular-nums">{day(r.last_contact) || DASH}</TableCell>
      <TableCell>{r.needs_check ? <Pill tone="warning">{r.needs_check} to check</Pill> : DASH}</TableCell>
      <TableCell className="pr-4 text-muted-foreground"><ChevronRight aria-hidden className="h-4 w-4 shrink-0 transition-transform group-hover:translate-x-0.5" /></TableCell>
    </TableRow>
  );
}
