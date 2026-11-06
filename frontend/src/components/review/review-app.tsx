"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ListChecks, ScanSearch } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { statusQuery } from "@/lib/api";
import { LogPanel } from "@/components/scrape/log-panel";
import { MailboxBoard } from "@/components/scrape/mailbox-board";
import { ScrapeForm } from "@/components/scrape/scrape-form";
import { StatusBar } from "@/components/scrape/status-bar";
import type { Filters } from "@/lib/api";
import { STAGES, STATUSES } from "@/lib/types";
import { Pick } from "./pick";
import { RecordDetail } from "./record-detail";
import { RecordsTable } from "./records-table";

const STATE_OPTIONS = [
  { value: "pending", label: "Pending review" }, { value: "approved", label: "Approved (staging)" },
  { value: "rejected", label: "Rejected" }, { value: "merged", label: "Merged" },
];
const STAGE_OPTIONS = [{ value: "", label: "Any stage" }, ...STAGES.map((s) => ({ value: s, label: s }))];
const STATUS_OPTIONS = [{ value: "", label: "Any status" }, ...STATUSES.map((s) => ({ value: s, label: s }))];

export function ReviewApp() {
  const router = useRouter(), pathname = usePathname(), params = useSearchParams();
  const filters: Filters = {
    state: params.get("state") || "pending", stage: params.get("stage") || "", status: params.get("status") || "",
    department: params.get("department") || "", q: params.get("q") || "",
  };
  const selectedId = Number(params.get("id")) || null;
  const tab = params.get("tab") === "scraping" ? "scraping" : "review";
  const logMailbox = params.get("mailbox") || "";
  const { data: status } = useQuery(statusQuery);
  const pending = status?.candidates.pending;

  const setParams = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) if (v) next.set(k, v); else next.delete(k);
    router.replace(`${pathname}?${next}`, { scroll: false });
  };

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
          <TabsContent value="review" className="pt-2">
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
            <section aria-label="Records" className="flex min-w-0 flex-col gap-3 rounded-xl border bg-card p-3">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                <Pick label="Review state" value={filters.state} options={STATE_OPTIONS} onChange={(v) => setParams({ state: v, id: null })} />
                <Pick label="Stage" value={filters.stage} options={STAGE_OPTIONS} onChange={(v) => setParams({ stage: v })} />
                <Pick label="Status" value={filters.status} options={STATUS_OPTIONS} onChange={(v) => setParams({ status: v })} />
                <Input aria-label="Department" placeholder="Department" value={dept} onChange={(e) => setDept(e.target.value)} />
                <Input aria-label="Search name or email" placeholder="Search name or email" value={q} onChange={(e) => setQ(e.target.value)}
                  className="col-span-2" />
              </div>
              <RecordsTable filters={filters} selectedId={selectedId} onSelect={(id) => setParams({ id: String(id) })} />
            </section>
            <section aria-label="Record detail" className="min-w-0 rounded-xl border bg-card p-4">
              {selectedId
                ? <RecordDetail key={selectedId} id={selectedId} onDone={() => setParams({ id: null })} />
                : <p className="py-8 text-center text-sm text-muted-foreground">Select a record to review.</p>}
            </section>
          </div>
          </TabsContent>
        </Tabs>
      </main>
    </>
  );
}
