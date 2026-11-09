"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ListChecks, ScanSearch, Search, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { candidatesQuery, statusQuery } from "@/lib/api";
import { LogPanel } from "@/components/scrape/log-panel";
import { MailboxBoard } from "@/components/scrape/mailbox-board";
import { ScrapeForm } from "@/components/scrape/scrape-form";
import { StatusBar } from "@/components/scrape/status-bar";
import { PAGE_SIZE, type Filters } from "@/lib/api";
import { STAGES, STATUSES, type ReviewState } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Pick } from "./pick";
import { RecordDetail } from "./record-detail";
import { RecordsTable } from "./records-table";

// Filter pills (vault filter-pill-set): idle muted, active tinted primary.
const PILL = "h-8 w-auto min-w-32 rounded-full bg-muted/40 text-sm";
const PILL_ON = "border-primary bg-primary/10 text-primary";
const STATE_OPTIONS = [
  { value: "pending", label: "Pending" }, { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" }, { value: "merged", label: "Merged" },
];
const STAGE_OPTIONS = [{ value: "", label: "Any stage" }, ...STAGES.map((s) => ({ value: s, label: s }))];
const STATUS_OPTIONS = [{ value: "", label: "Any status" }, ...STATUSES.map((s) => ({ value: s, label: s }))];
const RESETS_PAGE = new Set(["state", "stage", "status", "department", "q", "sort", "order"]);

export function ReviewApp() {
  const router = useRouter(), pathname = usePathname(), params = useSearchParams();
  const filters: Filters = {
    state: params.get("state") || "pending", stage: params.get("stage") || "", status: params.get("status") || "",
    department: params.get("department") || "", q: params.get("q") || "",
    // Unknown URL values fall back to the API default (newest last contact first) instead of a 400.
    sort: params.get("sort") === "first_contact" ? "first_contact" : "last_contact",
    order: params.get("order") === "asc" ? "asc" : "desc",
    page: Math.max(1, Math.floor(Number(params.get("page"))) || 1),
  };
  const selectedId = Number(params.get("id")) || null;
  const tab = params.get("tab") === "scraping" ? "scraping" : "review";
  const logMailbox = params.get("mailbox") || "";
  const { data: status } = useQuery(statusQuery);
  const pending = status?.candidates.pending;
  const { data: list } = useQuery(candidatesQuery(filters));
  const ids = list?.rows.map((r) => r.id) ?? [];
  const pos = selectedId ? ids.indexOf(selectedId) : -1;
  const filtered = !!(filters.stage || filters.status || filters.department || filters.q);

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
  const [q, setQ] = useState(filters.q), [dept, setDept] = useState(filters.department);
  useEffect(() => {
    const t = setTimeout(() => {
      if (q !== filters.q || dept !== filters.department) setParams({ q, department: dept });
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only typing should trigger this
  }, [q, dept]);

  return (
    <>
      <StatusBar />
      <main className="p-4 lg:p-6">
        <Tabs value={tab} onValueChange={(v) => setParams({ tab: v === "review" ? null : String(v) })}>
          <TabsList aria-label="Sections">
            <TabsTrigger value="review"><ListChecks className="h-4 w-4 shrink-0" /> Review{pending ? ` (${pending.toLocaleString()})` : ""}</TabsTrigger>
            <TabsTrigger value="scraping"><ScanSearch className="h-4 w-4 shrink-0" /> Scraping</TabsTrigger>
          </TabsList>
          <TabsContent value="scraping" className="pt-2">
            <div className="grid gap-4 xl:grid-cols-[minmax(0,24rem)_minmax(0,1fr)]">
              <div className="flex min-w-0 flex-col gap-4">
                <ScrapeForm />
                <MailboxBoard selected={logMailbox} onSelect={(mb) => setParams({ mailbox: mb || null })} />
              </div>
              <LogPanel mailbox={logMailbox} onClearMailbox={() => setParams({ mailbox: null })} />
            </div>
          </TabsContent>
          <TabsContent value="review" className="flex flex-col gap-4 pt-2">
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
                      {n != null && <span className={cn("rounded-full px-1.5 text-xs tabular-nums", active ? "bg-primary/10 text-primary" : "bg-muted")}>{n.toLocaleString()}</span>}
                    </button>
                  );
                })}
              </div>
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
                <div className="flex flex-wrap items-center gap-2">
                  <div className="relative">
                    <Search aria-hidden className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 shrink-0 -translate-y-1/2 text-muted-foreground" />
                    <Input aria-label="Search name, email or company" placeholder="Search clients" value={q} onChange={(e) => setQ(e.target.value)}
                      className="h-8 w-64 rounded-full pl-9" />
                  </div>
                  <Pick label="Stage" value={filters.stage} options={STAGE_OPTIONS} onChange={(v) => setParams({ stage: v })}
                    className={cn(PILL, filters.stage && PILL_ON)} />
                  <Pick label="Status" value={filters.status} options={STATUS_OPTIONS} onChange={(v) => setParams({ status: v })}
                    className={cn(PILL, filters.status && PILL_ON)} />
                  <Input aria-label="Department" placeholder="Department" value={dept} onChange={(e) => setDept(e.target.value)}
                    className={cn("h-8 w-36 rounded-full", dept && PILL_ON)} />
                  {filtered && (
                    <Button size="sm" variant="outline" className="h-8 rounded-full text-muted-foreground hover:border-destructive/40 hover:text-destructive"
                      onClick={() => { setQ(""); setDept(""); setParams({ stage: null, status: null, q: null, department: null }); }}>
                      <X className="h-4 w-4 shrink-0" /> Clear filters
                    </Button>
                  )}
                </div>
              } />
            <Sheet open={!!selectedId} onOpenChange={(open) => { if (!open) setParams({ id: null }); }}>
              <SheetContent showCloseButton={false} className="gap-0 p-0 data-[side=right]:w-full data-[side=right]:sm:max-w-2xl data-[side=right]:xl:max-w-3xl">
                {selectedId && (
                  <RecordDetail key={selectedId} id={selectedId} onDone={() => setParams({ id: null })}
                    prevId={ids[pos - 1] ?? null} nextId={pos >= 0 ? ids[pos + 1] ?? null : null}
                    onNav={(id) => setParams({ id: String(id) })} position={pos >= 0 && list ? `${((filters.page - 1) * PAGE_SIZE + pos + 1).toLocaleString()} of ${list.total.toLocaleString()}` : null} />
                )}
              </SheetContent>
            </Sheet>
          </TabsContent>
        </Tabs>
      </main>
    </>
  );
}
