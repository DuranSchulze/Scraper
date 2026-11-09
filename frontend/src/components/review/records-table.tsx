"use client";

import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, ArrowUpDown, BookUser, ChevronRight, FileSpreadsheet, Mail } from "lucide-react";
import { badgeVariants } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import {
  Pagination, PaginationContent, PaginationEllipsis, PaginationItem, PaginationLink, PaginationNext, PaginationPrevious,
} from "@/components/ui/pagination";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { candidatesQuery, PAGE_SIZE, type Filters, type SortKey } from "@/lib/api";
import { day, money } from "@/lib/format";
import type { CandidateRow } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Pill, StageBadge } from "./badges";
import { PhoneNumber } from "./phone";

const DASH = <span className="text-muted-foreground">—</span>;
// The pinned Client cell needs an opaque background, or scrolled cells show through it.
const PINNED = "sticky left-0 z-10 bg-card group-hover:bg-[color-mix(in_oklab,var(--muted)_40%,var(--card))] group-aria-selected:bg-accent";
const SOURCES = [
  { key: "gmail", label: "emails", icon: Mail },
  { key: "contacts", label: "contacts", icon: BookUser },
  { key: "sheets", label: "sheet rows", icon: FileSpreadsheet },
] as const;

/** Page numbers to show: first, last, and the current page with one neighbour each side; gaps become "…". */
export function pageList(page: number, pages: number): (number | "…")[] {
  const keep = [...new Set([1, page - 1, page, page + 1, pages])].filter((p) => p >= 1 && p <= pages).sort((a, b) => a - b);
  // A gap of exactly one page shows that page instead of an ellipsis.
  return keep.flatMap((p, i) => i === 0 || p - keep[i - 1] === 1 ? [p] : p - keep[i - 1] === 2 ? [p - 1, p] : ["…" as const, p]);
}

export function RecordsTable({ filters, selectedId, onSelect, onSort, onPage, pageHref, toolbar }: {
  filters: Filters; selectedId: number | null; onSelect: (id: number) => void; onSort: (key: SortKey) => void;
  onPage: (page: number) => void; pageHref: (page: number) => string; toolbar?: React.ReactNode;
}) {
  const tableRef = useRef<HTMLTableElement>(null);
  // A new page starts at the top of the scrolling table.
  useEffect(() => { tableRef.current?.parentElement?.scrollTo({ top: 0 }); }, [filters.page]);
  const sortHead = (key: SortKey, label: string) => {
    const active = filters.sort === key;
    const Icon = !active ? ArrowUpDown : filters.order === "asc" ? ArrowUp : ArrowDown;
    return (
      <TableHead className="w-32" aria-sort={active ? (filters.order === "asc" ? "ascending" : "descending") : "none"}>
        <button type="button" onClick={() => onSort(key)}
          title={active && filters.order === "asc" ? "Oldest first. Click for newest first" : "Click to sort oldest first"}
          className={cn("-ml-1.5 inline-flex items-center gap-1 rounded-md px-1.5 py-1 hover:bg-background hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring",
            active && "text-foreground")}>
          {label}<Icon aria-hidden className={cn("h-3.5 w-3.5 shrink-0", !active && "opacity-50")} />
        </button>
      </TableHead>
    );
  };
  const { data, error, isPending, isFetching, isPlaceholderData } = useQuery(candidatesQuery(filters));
  const rows = data?.rows;
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  const from = (filters.page - 1) * PAGE_SIZE;
  // Real links (middle-click opens a new tab); a plain click pages in place without a reload.
  const pageLink = (p: number, disabled: boolean) => disabled
    ? { "aria-disabled": true, tabIndex: -1, className: "pointer-events-none opacity-50" }
    : { href: pageHref(p), onClick: (e: React.MouseEvent) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault(); onPage(p);
      } };
  return (
    <Card className="gap-0 overflow-hidden py-0">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/60 px-4 py-3">
        <div className="flex items-baseline gap-2">
          <h2 className="text-base font-medium text-muted-foreground">Records</h2>
          <span className="text-sm font-semibold text-primary tabular-nums" aria-live="polite">
            {data ? data.total.toLocaleString() : "…"}
          </span>
        </div>
        {toolbar}
      </div>
      <Table ref={tableRef} className="min-w-[1300px]" containerClassName="max-h-[calc(100vh-15rem)] min-h-[16rem] overflow-auto">
          <TableHeader className="sticky top-0 z-20 bg-muted [&_th]:h-9 [&_th]:px-3 [&_th]:text-xs [&_th]:font-medium [&_th]:text-muted-foreground">
            <TableRow className="hover:bg-transparent">
              <TableHead className="sticky left-0 z-10 w-72 bg-muted pl-4">Client</TableHead>
              <TableHead className="w-48">Phone</TableHead>
              <TableHead className="w-72">Services</TableHead>
              <TableHead className="w-28">Stage</TableHead>
              <TableHead className="w-24">Status</TableHead>
              <TableHead className="w-36 text-right">Latest amount</TableHead>
              <TableHead className="w-20">Paid</TableHead>
              <TableHead className="w-40" title="Scraped emails, contacts and sheet rows behind this record">Scraped</TableHead>
              {sortHead("first_contact", "First contact")}
              {sortHead("last_contact", "Last contact")}
              <TableHead className="w-24">Checks</TableHead>
              <TableHead className="w-8 pr-4"><span className="sr-only">Open</span></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody className={cn("transition-opacity [&_td]:px-3 [&_td]:py-2.5", ((isPending && !rows) || (isFetching && isPlaceholderData)) && "opacity-60")}>
            {error && <TableRow><TableCell colSpan={12} className="py-10 text-center text-destructive">{error.message}</TableCell></TableRow>}
            {isPending && !error && <TableRow><TableCell colSpan={12} className="py-10 text-center text-muted-foreground">Loading records…</TableCell></TableRow>}
            {rows?.length === 0 && <TableRow><TableCell colSpan={12} className="py-10 text-center text-muted-foreground">No records match these filters.</TableCell></TableRow>}
            {rows?.map((r) => <Row key={r.id} r={r} selected={r.id === selectedId} onSelect={onSelect} />)}
          </TableBody>
      </Table>
      {data && data.total > PAGE_SIZE && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border/60 px-4 py-2.5">
          <p className="text-sm text-muted-foreground tabular-nums" aria-live="polite">
            Showing <span className="font-medium text-foreground">{(from + 1).toLocaleString()}–{Math.min(from + PAGE_SIZE, data.total).toLocaleString()}</span> of{" "}
            <span className="font-medium text-foreground">{data.total.toLocaleString()}</span> records
          </p>
          <Pagination aria-label="Records pages" className="mx-0 w-auto justify-end">
            <PaginationContent>
              <PaginationItem>
                <PaginationPrevious {...pageLink(filters.page - 1, filters.page <= 1)} />
              </PaginationItem>
              {pageList(filters.page, pages).map((p, i) => (
                <PaginationItem key={p === "…" ? `gap${i}` : p}>
                  {p === "…" ? <PaginationEllipsis /> : (
                    <PaginationLink isActive={p === filters.page} className="tabular-nums" {...pageLink(p, false)}>{p.toLocaleString()}</PaginationLink>
                  )}
                </PaginationItem>
              ))}
              <PaginationItem>
                <PaginationNext {...pageLink(filters.page + 1, filters.page >= pages)} />
              </PaginationItem>
            </PaginationContent>
          </Pagination>
        </div>
      )}
    </Card>
  );
}

const TIP_MAX = 10;

/** Tooltip list, capped so it fits on screen; the record details show everything. */
function tipList(items: string[], render: (item: string) => React.ReactNode) {
  return (
    <ul className="flex flex-col gap-1">
      {items.slice(0, TIP_MAX).map((item) => <li key={item}>{render(item)}</li>)}
      {items.length > TIP_MAX && <li className="opacity-70">and {items.length - TIP_MAX} more. Open the record to see all.</li>}
    </ul>
  );
}

/** A badge that shows a tooltip on hover or focus. Clicks and Enter stay on the badge, not the row. */
function TipBadge({ variant = "outline", label, tip, className, children }: {
  variant?: "outline" | "secondary"; label?: string; tip: React.ReactNode; className?: string; children: React.ReactNode;
}) {
  const stop = (e: React.SyntheticEvent) => e.stopPropagation();
  return (
    <Tooltip>
      <TooltipTrigger type="button" aria-label={label} onClick={stop} onKeyDown={stop}
        className={cn(badgeVariants({ variant }), "cursor-default tabular-nums", className)}>
        {children}
      </TooltipTrigger>
      <TooltipContent className="items-start">{tip}</TooltipContent>
    </Tooltip>
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
      <TableCell className="text-sm">
        {r.phones.length ? (
          <div className="flex items-center gap-1.5">
            <PhoneNumber raw={r.phones[0]} />
            {r.phones.length > 1 && (
              <TipBadge label={`${r.phones.length - 1} more phone numbers`}
                tip={tipList(r.phones, (p) => <PhoneNumber raw={p} />)}>
                +{r.phones.length - 1}
              </TipBadge>
            )}
          </div>
        ) : DASH}
      </TableCell>
      <TableCell>
        {r.services.length ? (
          <div className="flex w-64 flex-wrap gap-1">
            {r.services.slice(0, 2).map((s) => (
              <TipBadge key={s} variant="secondary" tip={s}
                className="h-auto max-w-full justify-start rounded-md whitespace-normal py-0.5 text-left leading-snug">
                {s}
              </TipBadge>
            ))}
            {more.length > 0 && (
              <TipBadge label={`${more.length} more services`}
                tip={tipList(more, (s) => s)}>
                +{more.length}
              </TipBadge>
            )}
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
