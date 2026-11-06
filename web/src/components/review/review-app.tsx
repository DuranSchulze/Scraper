"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Input } from "@/components/ui/input";
import type { Filters } from "@/lib/api";
import { STAGES, STATUSES } from "@/lib/types";
import { JobPanel } from "./job-panel";
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
      <JobPanel />
      <main className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] lg:p-6">
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
      </main>
    </>
  );
}
