"use client";

import { ExternalLink, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { day, money, tenure } from "@/lib/format";
import type { Handler } from "@/lib/types";
import { cn } from "@/lib/utils";
import { BASIS, Pill } from "./badges";
import { payRow, readPayments, type Draft, type PayRow } from "./draft";

const KIND: Record<string, string> = { confirmed: "staff confirmed", claim: "client message", screenshot: "client image" };

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border p-3">
      <p className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">{label}</p>
      <p className="mt-1 text-base font-semibold break-words tabular-nums">{value}</p>
    </div>
  );
}

export function ClientDetails({ draft, set, mentioned, handlers, lastContact, onSource }: {
  draft: Draft; set: (patch: Partial<Draft>) => void; mentioned: string[]; handlers: Handler[];
  lastContact: string | null; onSource: (id: number) => void;
}) {
  const pays = readPayments(draft.payments);
  const totals: Record<string, number> = {};
  for (const p of pays) if (p.amount !== null && p.currency) totals[p.currency] = (totals[p.currency] ?? 0) + p.amount;
  const known = Object.entries(totals).map(([currency, amount]) => money({ currency, amount })).join(" + ");
  const unknown = pays.filter((p) => p.amount === null).length;
  const totalPaid = !pays.length && !draft.paid ? "Not a paying client yet"
    : !known ? "Amount unknown" : known + (unknown ? ` (+${unknown} unknown)` : "");
  const dates = pays.map((p) => p.date).filter((d): d is string => !!d).sort();
  const since = dates[0];
  const end = [lastContact, dates.at(-1)].filter((d): d is string => !!d).sort().at(-1);

  const setRow = (key: string, patch: Partial<PayRow>) =>
    set({ payments: draft.payments.map((r) => r.key === key ? { ...r, ...patch } : r) });
  // Editing an extracted amount makes it the reviewer's ("manual"); clearing it makes it unknown.
  const editAmount = (r: PayRow, patch: Partial<PayRow>) => {
    const amount = patch.amount ?? r.amount;
    setRow(r.key, { ...patch, basis: amount === "" ? "unknown" : "manual" });
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3" aria-live="polite">
        <Stat label="Total paid" value={totalPaid} />
        <Stat label="Client since" value={since ? `${day(since)} · ${tenure(since, end)}` : "—"} />
        <Stat label="Department now" value={draft.department.trim() || "—"} />
      </div>

      <h4 id="payHead" className="text-sm font-medium">Payments</h4>
      <div role="group" aria-labelledby="payHead" className="flex flex-col">
        {draft.payments.length === 0 && <p className="text-sm text-muted-foreground">No payment found in the emails.</p>}
        {draft.payments.map((r, i) => {
          const flagged = r.basis === "invoice" || r.basis === "unknown";
          const b = BASIS[r.basis];
          return (
            <div key={r.key} className={cn("flex flex-wrap items-center gap-2 border-b py-2", flagged && "-mx-2 rounded-lg bg-warning/10 px-2")}>
              <Input type="date" aria-label={`Payment ${i + 1} date`} value={r.date} className="w-40"
                onChange={(e) => setRow(r.key, { date: e.target.value })} />
              <Input type="number" min="0.01" step="0.01" aria-label={`Payment ${i + 1} amount`} value={r.amount} className="w-32"
                onChange={(e) => editAmount(r, { amount: e.target.value })} />
              <Input aria-label={`Payment ${i + 1} currency`} value={r.currency} maxLength={3} list="currencies" className="w-20 uppercase"
                onChange={(e) => editAmount(r, { currency: e.target.value })} />
              <Pill tone={b.tone}>{b.label}</Pill>
              {r.kind && <span className="text-xs text-muted-foreground">{KIND[r.kind] ?? r.kind}</span>}
              <div className="ml-auto flex gap-1">
                {r.raw_item_id && (
                  <Button type="button" variant="ghost" size="sm" onClick={() => onSource(r.raw_item_id!)}>
                    <ExternalLink className="h-4 w-4 shrink-0" /> Source
                  </Button>
                )}
                <Button type="button" variant="ghost" size="sm" aria-label={`Remove payment ${i + 1}`}
                  className="text-destructive hover:bg-destructive/10"
                  onClick={() => set({ payments: draft.payments.filter((x) => x.key !== r.key) })}>
                  <Trash2 className="h-4 w-4 shrink-0" /> Remove
                </Button>
              </div>
            </div>
          );
        })}
        <datalist id="currencies"><option value="PHP" /><option value="USD" /></datalist>
      </div>
      <Button type="button" variant="outline" size="sm" className="self-start"
        onClick={() => set({ payments: [...draft.payments, payRow({ basis: "manual" })] })}>
        <Plus className="h-4 w-4 shrink-0" /> Add payment
      </Button>

      <div className="flex flex-col gap-1">
        <Label htmlFor="d_availed" className="text-xs text-muted-foreground">Services availed (comma list)</Label>
        <Input id="d_availed" value={draft.availed} onChange={(e) => set({ availed: e.target.value })} />
        <p className="text-xs text-muted-foreground">
          {mentioned.length ? `Mentioned in emails: ${mentioned.join(", ")}` : "No services mentioned in emails."}
        </p>
      </div>

      <h4 className="text-sm font-medium">Department history</h4>
      {handlers.length ? (
        <div className="overflow-x-auto">
          <Table className="min-w-[460px]">
            <TableHeader><TableRow>
              <TableHead>Department</TableHead><TableHead>Staff mailbox</TableHead>
              <TableHead>First</TableHead><TableHead>Last</TableHead><TableHead className="text-right">Emails</TableHead>
            </TableRow></TableHeader>
            <TableBody>
              {handlers.map((h) => (
                <TableRow key={`${h.department}|${h.mailbox}`}>
                  <TableCell>{h.department || "Not mapped"}</TableCell>
                  <TableCell>{h.mailbox || "—"}</TableCell>
                  <TableCell className="tabular-nums">{day(h.first) || "—"}</TableCell>
                  <TableCell className="tabular-nums">{day(h.last) || "—"}</TableCell>
                  <TableCell className="text-right tabular-nums">{h.messages}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : <p className="text-sm text-muted-foreground">No staff mailbox handled this client.</p>}
    </div>
  );
}
