"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { AtSign, Search, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Input } from "@/components/ui/input";
import { candidatesQuery, facetsQuery, FILTER_KEYS, PAGE_SIZE, statusQuery, type Filters } from "@/lib/api";
import { STAGES, STATUSES, type ReviewState } from "@/lib/types";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/shell/page-header";
import { DateRangeFilter } from "./date-range";
import { Pick } from "./pick";
import { RecordDetail } from "./record-detail";
import { RecordsTable } from "./records-table";

// Filter pills (vault filter-pill-set): idle muted, active tinted primary.
const PILL = "h-8 w-auto min-w-32 rounded-full bg-muted/40 text-sm";
const PILL_ON = "border-primary bg-primary/10 text-primary-text";
const STATE_OPTIONS = [
  { value: "pending", label: "Pending" }, { value: "approved", label: "Confirmed" },
  { value: "rejected", label: "Rejected" }, { value: "merged", label: "Merged" },
];
const STAGE_OPTIONS = [{ value: "", label: "Any stage" }, ...STAGES.map((s) => ({ value: s, label: s }))];
const STATUS_OPTIONS = [{ value: "", label: "Any status" }, ...STATUSES.map((s) => ({ value: s, label: s }))];
const RESETS_PAGE = new Set<string>(["state", "sort", "order", ...FILTER_KEYS]);
const any = (label: string, values: string[] = []) => [{ value: "", label }, ...values.map((v) => ({ value: v, label: v }))];

export function ReviewApp() {
  const router = useRouter(), pathname = usePathname(), params = useSearchParams();
  const filters: Filters = {
    state: params.get("state") || "pending",
    ...Object.fromEntries(FILTER_KEYS.map((k) => [k, params.get(k) || ""])) as Record<(typeof FILTER_KEYS)[number], string>,
    // Unknown URL values fall back to the API default (newest last contact first) instead of a 400.
    sort: params.get("sort") === "first_contact" ? "first_contact" : "last_contact",
    order: params.get("order") === "asc" ? "asc" : "desc",
    page: Math.max(1, Math.floor(Number(params.get("page"))) || 1),
  };
  const selectedId = Number(params.get("id")) || null;
  const { data: status } = useQuery(statusQuery);
  const { data: list } = useQuery(candidatesQuery(filters));
  const ids = list?.rows.map((r) => r.id) ?? [];
  const pos = selectedId ? ids.indexOf(selectedId) : -1;
  const { data: facets } = useQuery(facetsQuery(filters.state));
  const filtered = FILTER_KEYS.some((k) => filters[k]);
  const toggle = (k: "has_phone" | "has_amount") => setParams({ [k]: filters[k] ? null : "true" });

  const href = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) if (v) next.set(k, v); else next.delete(k);
    // Any filter or sort change starts again at page 1.
    if (Object.keys(patch).some((k) => RESETS_PAGE.has(k))) next.delete("page");
    return `${pathname}?${next}`;
  };
  const setParams = (patch: Record<string, string | null>) => router.replace(href(patch), { scroll: false });
  const pagePatch = (p: number) => ({ page: p > 1 ? String(p) : null });

  // Reviewing records shrinks the list; step back if the current page no longer exists.
  const lastPage = list ? Math.max(1, Math.ceil(list.total / PAGE_SIZE)) : null;
  useEffect(() => {
    if (lastPage && filters.page > lastPage) setParams(pagePatch(lastPage));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only a changed total should trigger this
  }, [lastPage, filters.page]);

  // Text filters update the URL 300 ms after typing stops.
  const [q, setQ] = useState(filters.q), [domain, setDomain] = useState(filters.domain);
  // When the URL changes from elsewhere (nav link, back button), adopt it unless the box holds unsent typing.
  const [seen, setSeen] = useState({ q: filters.q, domain: filters.domain });
  if (seen.q !== filters.q || seen.domain !== filters.domain) {
    setSeen({ q: filters.q, domain: filters.domain });
    if (q === seen.q) setQ(filters.q);
    if (domain === seen.domain) setDomain(filters.domain);
  }
  useEffect(() => {
    const t = setTimeout(() => {
      if (q !== filters.q || domain !== filters.domain) setParams({ q, domain });
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only typing should trigger this
  }, [q, domain]);

  return (
    <div className="flex flex-col gap-4">
      <PageHeader title="Review" description="Confirm each client's details, then approve or reject. Shortcuts in a record: A approve, R reject, J / K next / previous." />
      <div className="flex flex-wrap items-center gap-2">
        <div role="group" aria-label="Review state" className="inline-flex max-w-full overflow-x-auto rounded-full border bg-muted/40 p-0.5">
          {STATE_OPTIONS.map((o) => {
            const active = filters.state === o.value;
            const n = status?.candidates[o.value as ReviewState];
            return (
              <button key={o.value} type="button" aria-pressed={active} onClick={() => setParams({ state: o.value === "pending" ? null : o.value, id: null })}
                className={cn("inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full px-3 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-ring",
                  active ? "bg-card text-foreground shadow-xs" : "text-muted-foreground hover:text-foreground")}>
                {o.label}
                {n != null && <span className={cn("rounded-full px-1.5 text-xs tabular-nums", active ? "bg-primary/10 text-primary-text" : "bg-muted")}>{n.toLocaleString()}</span>}
              </button>
            );
          })}
        </div>
      </div>
      <div role="group" aria-label="Filters" className="flex flex-wrap items-center gap-2">
        <Pick label="Stage" value={filters.stage} options={STAGE_OPTIONS} onChange={(v) => setParams({ stage: v })}
          className={cn(PILL, filters.stage && PILL_ON)} />
        <Pick label="Status" value={filters.status} options={STATUS_OPTIONS} onChange={(v) => setParams({ status: v })}
          className={cn(PILL, filters.status && PILL_ON)} />
        <Pick label="Department" value={filters.department} options={any("Any department", facets?.departments)}
          onChange={(v) => setParams({ department: v })} className={cn(PILL, "max-w-56", filters.department && PILL_ON)} />
        <Pick label="Staff mailbox" value={filters.mailbox} options={any("Any mailbox", facets?.mailboxes)}
          onChange={(v) => setParams({ mailbox: v })} className={cn(PILL, "max-w-64", filters.mailbox && PILL_ON)} />
        <Pick label="Service" value={filters.service} options={any("Any service", facets?.services)}
          onChange={(v) => setParams({ service: v })} className={cn(PILL, "max-w-64", filters.service && PILL_ON)} />
        <div className="relative">
          <AtSign aria-hidden className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 shrink-0 -translate-y-1/2 text-muted-foreground" />
          <Input aria-label="Email domain" placeholder="company.com" value={domain} onChange={(e) => setDomain(e.target.value)}
            className={cn("h-8 w-40 rounded-full pl-9", domain && PILL_ON)} />
        </div>
        <DateRangeFilter from={filters.date_from} to={filters.date_to}
          onChange={(from, to) => setParams({ date_from: from, date_to: to })}
          className={cn("h-8 rounded-full bg-muted/40 font-normal", (filters.date_from || filters.date_to) && PILL_ON)} />
        {(["has_phone", "has_amount"] as const).map((k) => (
          <Button key={k} size="sm" variant="outline" aria-pressed={!!filters[k]} onClick={() => toggle(k)}
            className={cn("h-8 rounded-full bg-muted/40 font-normal", filters[k] && PILL_ON)}>
            {k === "has_phone" ? "Has phone" : "Has amount"}
          </Button>
        ))}
        {filtered && (
          <Button size="sm" variant="outline" className="h-8 rounded-full text-muted-foreground hover:border-destructive/40 hover:text-destructive"
            onClick={() => { setQ(""); setDomain(""); setParams(Object.fromEntries(FILTER_KEYS.map((k) => [k, null]))); }}>
            <X className="h-4 w-4 shrink-0" /> Clear filters
          </Button>
        )}
      </div>
      <RecordsTable filters={filters} selectedId={selectedId} onSelect={(id) => setParams({ id: String(id) })}
        onPage={(p) => setParams(pagePatch(p))} pageHref={(p) => href(pagePatch(p))}
        onSort={(sort) => {
          // A new column starts oldest first; the active column flips. The default (last contact, newest) leaves the URL clean.
          const order = sort === filters.sort && filters.order === "asc" ? "desc" : "asc";
          const isDefault = sort === "last_contact" && order === "desc";
          setParams({ sort: isDefault ? null : sort, order: isDefault ? null : order });
        }}
        toolbar={
          <div className="relative">
            <Search aria-hidden className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 shrink-0 -translate-y-1/2 text-muted-foreground" />
            <Input aria-label="Search name, email or company" placeholder="Search clients" value={q} onChange={(e) => setQ(e.target.value)}
              className="h-8 w-64 max-w-full rounded-full pl-9" />
          </div>
        } />
            <Sheet open={!!selectedId} onOpenChange={(open) => { if (!open) setParams({ id: null }); }}>
              <SheetContent showCloseButton={false} className="gap-0 p-0 data-[side=right]:w-full data-[side=right]:sm:max-w-2xl data-[side=right]:xl:max-w-3xl">
                {selectedId && (
                  <RecordDetail key={selectedId} id={selectedId} onDone={(next) => setParams({ id: next ? String(next) : null })}
                    prevId={ids[pos - 1] ?? null} nextId={pos >= 0 ? ids[pos + 1] ?? null : null}
                    onNav={(id) => setParams({ id: String(id) })} position={pos >= 0 && list ? `${((filters.page - 1) * PAGE_SIZE + pos + 1).toLocaleString()} of ${list.total.toLocaleString()}` : null} />
                )}
              </SheetContent>
            </Sheet>
    </div>
  );
}
