"use client";

import { useQuery } from "@tanstack/react-query";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { candidatesQuery, type Filters } from "@/lib/api";
import { day } from "@/lib/format";
import { cn } from "@/lib/utils";
import { StageBadge } from "./badges";

/** Long names and emails are cut with an ellipsis; the full value shows on hover and in the detail panel. */
function Cut({ value, className }: { value: string; className?: string }) {
  return <span title={value} className={cn("block truncate", className)}>{value}</span>;
}

export function RecordsTable({ filters, selectedId, onSelect }: {
  filters: Filters; selectedId: number | null; onSelect: (id: number) => void;
}) {
  const { data: rows, error, isPending } = useQuery(candidatesQuery(filters));
  return (
    <div className="max-h-[70vh] overflow-auto">
      <Table className="table-fixed min-w-[780px]">
        <TableHeader className="sticky top-0 bg-card">
          <TableRow>
            <TableHead className="w-14">ID</TableHead>
            <TableHead className="w-40">Name</TableHead>
            <TableHead>Email / key</TableHead>
            <TableHead className="w-24 pl-4">Stage</TableHead>
            <TableHead className="w-20">Status</TableHead>
            <TableHead className="w-14">Paid</TableHead>
            <TableHead className="w-16">Checks</TableHead>
            <TableHead className="w-24">Last contact</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody className={cn(isPending && "opacity-60 transition-opacity")}>
          {error && <TableRow><TableCell colSpan={8} className="py-8 text-center text-destructive">{error.message}</TableCell></TableRow>}
          {rows?.length === 0 && <TableRow><TableCell colSpan={8} className="py-8 text-center text-muted-foreground">No records.</TableCell></TableRow>}
          {rows?.map((r) => (
            <TableRow key={r.id} tabIndex={0} aria-selected={r.id === selectedId}
              onClick={() => onSelect(r.id)} onKeyDown={(e) => e.key === "Enter" && onSelect(r.id)}
              className={cn("cursor-pointer hover:bg-muted/40 focus-visible:outline-2 focus-visible:outline-ring", r.id === selectedId && "bg-accent")}>
              <TableCell className="tabular-nums">{r.id}</TableCell>
              <TableCell><Cut value={r.name || "—"} /></TableCell>
              <TableCell><Cut value={r.email_key} className="text-muted-foreground" /></TableCell>
              <TableCell className="pl-4"><StageBadge stage={r.stage} /></TableCell>
              <TableCell><Cut value={r.status || "—"} /></TableCell>
              <TableCell>{r.paid ? "yes" : "no"}</TableCell>
              <TableCell className="font-medium text-warning tabular-nums">{r.needs_check ? `⚠ ${r.needs_check}` : ""}</TableCell>
              <TableCell className="tabular-nums">{day(r.last_contact) || "—"}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
