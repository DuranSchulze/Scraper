"use client";

import { format, isValid, parseISO } from "date-fns";
import { CalendarIcon, X } from "lucide-react";
import type { DateRange } from "react-day-picker";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

const iso = (d?: Date) => (d ? format(d, "yyyy-MM-dd") : "");
// Hand-edited URLs can hold bad dates; treat them as unset instead of crashing the page.
const parse = (s: string) => { const d = parseISO(s); return s && isValid(d) ? d : undefined; };
const short = (d: Date) => format(d, "MMM d, yyyy");

/** Last-contact range filter: values are YYYY-MM-DD strings ("" = open end). */
export function DateRangeFilter({ from, to, onChange, className }: {
  from: string; to: string; onChange: (from: string, to: string) => void; className?: string;
}) {
  const f = parse(from), t = parse(to);
  const label = f && t ? `${short(f)} – ${short(t)}` : f ? `From ${short(f)}` : t ? `Until ${short(t)}` : "Last contact";
  const range: DateRange | undefined = f || t ? { from: f, to: t } : undefined;
  return (
    <Popover>
      <PopoverTrigger render={<Button variant="outline" size="sm" aria-label={`Last contact date range: ${label}`} className={className} />}>
        <CalendarIcon aria-hidden className="h-4 w-4 shrink-0" />{label}
      </PopoverTrigger>
      <PopoverContent align="start" className="w-auto p-0">
        <Calendar mode="range" numberOfMonths={2} defaultMonth={f ?? t} selected={range}
          captionLayout="dropdown" startMonth={new Date(2015, 0)} endMonth={new Date()}
          onSelect={(r) => onChange(iso(r?.from), iso(r?.to))} />
        <div className="flex items-center justify-between gap-2 border-t px-3 py-2 text-xs text-muted-foreground">
          <span>Filters on each client&apos;s last contact.</span>
          <Button variant="ghost" size="sm" disabled={!from && !to} onClick={() => onChange("", "")}
            className={cn("h-7", range && "hover:text-destructive")}>
            <X className="h-3.5 w-3.5 shrink-0" /> Clear dates
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
