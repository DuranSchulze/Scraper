"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AtSign, BookUser, RefreshCw, Search, SlidersHorizontal, Sparkles, X } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Input } from "@/components/ui/input";
import { ApiError, candidatesQuery, facetsQuery, FILTER_KEYS, MORE_KEYS, masterSummaryQuery, PAGE_SIZE, post, statusQuery, type Bucket, type Filters } from "@/lib/api";
import { STAGES, STATUSES, type ReviewState } from "@/lib/types";
import { cn } from "@/lib/utils";
import { phDateTime } from "@/lib/format";
import { PageHeader } from "@/components/shell/page-header";
import { ConfirmDialog } from "./confirm-dialog";
import { GRACE_SECONDS, Spinner, useGuarded } from "@/lib/guarded";
import { DateRangeFilter } from "./date-range";
import { Pick } from "./pick";
import { RecordDetail } from "./record-detail";
import { RecordsTable } from "./records-table";

// Filter pills (vault filter-pill-set): idle muted, active tinted primary.
const PILL = "h-8 w-auto min-w-32 rounded-full border-transparent bg-muted/40 text-sm";
const PILL_ON = "bg-[color-mix(in_oklab,var(--primary)_16%,transparent)] text-primary-text";
// Toggle and popover chips: tonal surface, no outline; lift on hover, press on click.
const CHIP = "h-8 rounded-full border-transparent bg-muted/40 font-normal transition-transform hover:-translate-y-0.5 active:translate-y-0 active:scale-[0.97] motion-reduce:transform-none";
const STATE_OPTIONS = [
  { value: "pending", label: "Pending" }, { value: "approved", label: "Confirmed" },
  { value: "rejected", label: "Rejected" }, { value: "merged", label: "Merged" },
];
const STAGE_OPTIONS = [{ value: "", label: "Any stage" }, ...STAGES.map((s) => ({ value: s, label: s }))];
const STATUS_OPTIONS = [{ value: "", label: "Any status" }, ...STATUSES.map((s) => ({ value: s, label: s }))];
const MATCH_OPTIONS = [
  { value: "", label: "Any directory match" }, { value: "any", label: "In client directory" },
  { value: "email", label: "Exact email" }, { value: "domain", label: "Same domain" }, { value: "none", label: "Not in directory" },
];
const BUCKETS: { value: Bucket; label: string }[] = [{ value: "day", label: "Day" }, { value: "week", label: "Week" }];
const TOGGLES = ["new", "has_phone", "has_amount"] as const;
const RESETS_PAGE =new Set<string>(["state", "sort", "order", ...FILTER_KEYS]);
const any = (label: string, values: string[] = []) => [{ value: "", label }, ...values.map((v) => ({ value: v, label: v }))];

export function ReviewApp() {
  const router = useRouter(), pathname = usePathname(), params = useSearchParams();
  const state = params.get("state") || "pending";
  const filters: Filters = {
    state,
    ...Object.fromEntries(FILTER_KEYS.map((k) => [k, params.get(k) || ""])) as Record<(typeof FILTER_KEYS)[number], string>,
    // Directory matching exists only for pending records; a match in the URL of another tab is ignored.
    ...(state !== "pending" && { match: "" }),
    // Toggles are on only for "true"; ?new=false and the like count as off everywhere.
    ...Object.fromEntries(TOGGLES.map((k) => [k, params.get(k) === "true" ? "true" : ""])),
    // Unknown URL values fall back to the API default (newest last contact first) instead of a 400.
    sort: params.get("sort") === "first_contact" ? "first_contact" : "last_contact",
    order: params.get("order") === "asc" ? "asc" : "desc",
    page: Math.max(1, Math.floor(Number(params.get("page"))) || 1),
  };
  const bucket: Bucket = params.get("bucket") === "week" ? "week" : "day";
  const selectedId = Number(params.get("id")) || null;
  const { data: status } = useQuery(statusQuery);
  const { data: list, dataUpdatedAt: listAt } = useQuery(candidatesQuery(filters));
  const ids = list?.rows.map((r) => r.id) ?? [];
  const pos = selectedId ? ids.indexOf(selectedId) : -1;
  const { data: facets } = useQuery(facetsQuery(filters.state));
  const filtered = FILTER_KEYS.some((k) => filters[k]);
  const moreCount = MORE_KEYS.filter((k) => filters[k]).length;
  const toggle = (k: "has_phone" | "has_amount" | "new") => setParams({ [k]: filters[k] ? null : "true" });

  // A newly completed scrape changes which records are "New": refetch when status knows a newer scrape than the list
  // (compared on every response, including the first, so a completion between the two first reads is not missed).
  const qc = useQueryClient();
  const lastScrape = status?.last_complete_scrape_job ?? null;
  const listScrape = list?.scrapeJob ?? null;
  // listAt (0 until the list resolves) rechecks on every list response, also a late one with the same null generation.
  useEffect(() => {
    if (lastScrape !== null && listAt > 0 && (listScrape ?? 0) < lastScrape) qc.invalidateQueries({ queryKey: ["candidates"] });
  }, [lastScrape, listScrape, listAt, qc]);

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
      <PageHeader title="Review" description="Records grouped by date, tagged when a client directory contact has the same email or company domain. Shortcuts in a record: A approve, R reject, J / K next / previous." />
      <DirectoryLine />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <StateTabs value={filters.state} counts={status?.candidates}
          onChange={(v) => setParams({ state: v === "pending" ? null : v, id: null, ...(v !== "pending" && { match: null }) })} />
        <div role="group" aria-label="Group records by" className="inline-flex rounded-full bg-muted/40 p-0.5">
          {BUCKETS.map((b) => (
            <button key={b.value} type="button" aria-pressed={bucket === b.value} onClick={() => setParams({ bucket: b.value === "day" ? null : b.value })}
              className={cn("inline-flex h-7 items-center rounded-full px-3 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-ring",
                bucket === b.value ? "bg-card text-foreground shadow-xs" : "text-muted-foreground hover:text-foreground")}>
              By {b.label.toLowerCase()}
            </button>
          ))}
        </div>
      </div>
      <div role="group" aria-label="Filters" className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="ghost" aria-pressed={!!filters.new} onClick={() => toggle("new")}
          className={cn(CHIP, filters.new && PILL_ON)}>
          <Sparkles aria-hidden className="h-4 w-4 shrink-0" /> New only
        </Button>
        {filters.state === "pending" && (
          <Pick label="Client directory match" value={filters.match} options={MATCH_OPTIONS} onChange={(v) => setParams({ match: v })}
            className={cn(PILL, "max-w-56", filters.match && PILL_ON)} />
        )}
        <DateRangeFilter from={filters.date_from} to={filters.date_to}
          onChange={(from, to) => setParams({ date_from: from, date_to: to })}
          className={cn(CHIP, (filters.date_from || filters.date_to) && PILL_ON)} />
        <Popover>
          <PopoverTrigger render={<Button size="sm" variant="ghost" className={cn(CHIP, moreCount > 0 && PILL_ON)} />}
            aria-label={moreCount ? `More filters, ${moreCount} active` : "More filters"}>
            <SlidersHorizontal aria-hidden className="h-4 w-4 shrink-0" /> More filters
            {moreCount > 0 && <span className="rounded-full bg-primary px-1.5 text-xs font-semibold text-primary-foreground tabular-nums">{moreCount}</span>}
          </PopoverTrigger>
          <PopoverContent align="start" className="w-80 gap-3 p-3">
            <div className="grid grid-cols-2 gap-2">
              <Pick label="Stage" value={filters.stage} options={STAGE_OPTIONS} onChange={(v) => setParams({ stage: v })}
                className={cn(PILL, "w-full min-w-0", filters.stage && PILL_ON)} />
              <Pick label="Status" value={filters.status} options={STATUS_OPTIONS} onChange={(v) => setParams({ status: v })}
                className={cn(PILL, "w-full min-w-0", filters.status && PILL_ON)} />
            </div>
            <Pick label="Department" value={filters.department} options={any("Any department", facets?.departments)}
              onChange={(v) => setParams({ department: v })} className={cn(PILL, "w-full", filters.department && PILL_ON)} />
            <Pick label="Staff mailbox" value={filters.mailbox} options={any("Any mailbox", facets?.mailboxes)}
              onChange={(v) => setParams({ mailbox: v })} className={cn(PILL, "w-full", filters.mailbox && PILL_ON)} />
            <Pick label="Service" value={filters.service} options={any("Any service", facets?.services)}
              onChange={(v) => setParams({ service: v })} className={cn(PILL, "w-full", filters.service && PILL_ON)} />
            <div className="relative">
              <AtSign aria-hidden className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 shrink-0 -translate-y-1/2 text-muted-foreground" />
              <Input aria-label="Email domain" placeholder="company.com" value={domain} onChange={(e) => setDomain(e.target.value)}
                className={cn("h-8 w-full rounded-full border-transparent bg-muted/40 pl-9", domain && PILL_ON)} />
            </div>
            <div className="flex gap-2">
              {(["has_phone", "has_amount"] as const).map((k) => (
                <Button key={k} size="sm" variant="ghost" aria-pressed={!!filters[k]} onClick={() => toggle(k)}
                  className={cn(CHIP, "flex-1", filters[k] && PILL_ON)}>
                  {k === "has_phone" ? "Has phone" : "Has amount"}
                </Button>
              ))}
            </div>
          </PopoverContent>
        </Popover>
        {filtered && (
          <Button size="sm" variant="ghost" className="h-8 rounded-full text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
            onClick={() => { setQ(""); setDomain(""); setParams(Object.fromEntries(FILTER_KEYS.map((k) => [k, null]))); }}>
            <X className="h-4 w-4 shrink-0" /> Clear filters
          </Button>
        )}
      </div>
      <RecordsTable filters={filters} bucket={bucket} selectedId={selectedId} onSelect={(id) => setParams({ id: String(id) })}
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

/** Pending / Confirmed / Rejected / Merged: a solid brand pill slides under the selected tab. */
function StateTabs({ value, counts, onChange }: {
  value: string; counts?: Partial<Record<ReviewState, number>>; onChange: (v: string) => void;
}) {
  const refs = useRef(new Map<string, HTMLButtonElement>());
  const [box, setBox] = useState<{ left: number; width: number } | null>(null);
  // Measure the selected tab; counts change its width, so remeasure when they arrive.
  useLayoutEffect(() => {
    const el = refs.current.get(value);
    if (el) setBox({ left: el.offsetLeft, width: el.offsetWidth });
  }, [value, counts]);
  return (
    <div role="group" aria-label="Review state" className="relative inline-flex max-w-full overflow-x-auto rounded-full bg-muted/60 p-1">
      {box && (
        <span aria-hidden style={{ left: box.left, width: box.width }}
          className="absolute top-1 bottom-1 rounded-full bg-primary shadow-sm transition-[left,width] duration-300 ease-out motion-reduce:transition-none" />
      )}
      {STATE_OPTIONS.map((o) => {
        const active = value === o.value;
        const n = counts?.[o.value as ReviewState];
        return (
          <button key={o.value} type="button" aria-pressed={active} onClick={() => onChange(o.value)}
            ref={(el) => { if (el) refs.current.set(o.value, el); else refs.current.delete(o.value); }}
            className={cn("relative z-10 inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full px-3.5 text-sm transition-[color,transform] duration-200 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring active:scale-[0.97] motion-reduce:transform-none",
              active ? "font-semibold text-primary-foreground" : "font-medium text-muted-foreground hover:text-foreground")}>
            {o.label}
            {n != null && (
              <span className={cn("rounded-full px-1.5 text-xs tabular-nums",
                active ? "bg-[color-mix(in_oklab,var(--primary-foreground)_16%,transparent)]" : "bg-[color-mix(in_oklab,var(--muted-foreground)_16%,transparent)]")}>
                {n.toLocaleString()}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/** Which master list the tags come from and how fresh they are. */
function DirectoryLine() {
  const { data: m } = useQuery(masterSummaryQuery);
  if (!m) return null;
  return (
    <div className="-mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
    <p role="status" className="inline-flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
      <BookUser aria-hidden className="h-3.5 w-3.5 shrink-0" />
      {m.available ? (
        <>
          <span>Client directory: <span className="tabular-nums text-foreground">{m.companies.toLocaleString()}</span> companies, <span className="tabular-nums text-foreground">{m.contacts.toLocaleString()}</span> contacts.</span>
          <span><span className="tabular-nums text-foreground">{m.email.toLocaleString()}</span> pending records match by email, <span className="tabular-nums text-foreground">{m.domain.toLocaleString()}</span> by company domain.</span>
          {m.tagged_at && <span>Tagged {phDateTime(m.tagged_at)}; retagged after every scrape.</span>}
        </>
      ) : <span>The client directory tables are not in this database yet, so no records are tagged.</span>}
    </p>
    <SyncMatched count={m.approvable} />
    </div>
  );
}

/** Sync: one click approves every pending email match and every domain match to a single company, after a confirm. */
function SyncMatched({ count }: { count: number }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const guard = useGuarded("sync");
  const run = useMutation({
    // The count shown in the confirm travels with the request; the server refuses (409) if it changed meanwhile.
    mutationFn: (expected: number) => post<{ approved: number; skipped: { id: number; error: string }[] }>("/api/candidates/approve-matched", { expected }),
    onSuccess: (r) => {
      toast.success(`Synced ${r.approved.toLocaleString()} candidate${r.approved === 1 ? "" : "s"} with the client database.`);
      if (r.skipped.length) toast.error(`${r.skipped.length.toLocaleString()} could not be synced and stay pending.`,
        { description: r.skipped.slice(0, 3).map((s) => `#${s.id}: ${s.error}`).join("; ") });
      qc.invalidateQueries();
    },
    onError: (e: Error) => {
      // 409: the count changed or tagging is running; the server approved nothing, so refresh and let the user retry.
      toast.error(e instanceof ApiError && e.status === 409 ? `Nothing was synced: ${e.message}.` : e.message);
      qc.invalidateQueries();
    },
  });
  const n = count.toLocaleString();
  return (
    <>
      <Button size="sm" variant="outline" disabled={!count || guard.busy} aria-busy={guard.running} onClick={() => setOpen(true)}>
        {guard.busyLabel ? <><Spinner />{guard.busyLabel}</> : <><RefreshCw aria-hidden className="h-4 w-4 shrink-0" /> Sync {n} matched</>}
      </Button>
      <ConfirmDialog open={open} onOpenChange={(o) => !o && setOpen(false)} guard={guard}
        title={`Sync ${n} matched candidate${count === 1 ? "" : "s"} with the client database?`} confirmLabel={`Sync ${n}`}
        description={`Approves ${n} pending candidate${count === 1 ? "" : "s"} that match the client database uploaded in the Client Directory: exact email matches, and domain matches that point to one company. Domains shared by several companies stay pending. Synced candidates still need Publish to reach the clients table. You get ${GRACE_SECONDS} seconds to undo before anything is sent.`}
        onConfirm={() => {
          setOpen(false);
          guard.start({ verb: "Syncing", what: `Syncing ${n} matched candidate${count === 1 ? "" : "s"}`, run: () => run.mutateAsync(count) });
        }} />
    </>
  );
}
