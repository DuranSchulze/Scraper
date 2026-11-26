"use client";

import { useState } from "react";
import { differenceInCalendarDays, endOfMonth, format, isValid, parseISO, startOfMonth, subDays, subMonths } from "date-fns";
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

function getPresets(now: Date) {
  const prev = subMonths(now, 1);
  return [
    { label: "Today", from: iso(now), to: iso(now) },
    { label: "Last 7 days", from: iso(subDays(now, 6)), to: iso(now) },
    { label: "Last 30 days", from: iso(subDays(now, 29)), to: iso(now) },
    { label: "This month", from: iso(startOfMonth(now)), to: iso(now) },
    { label: "Last month", from: iso(startOfMonth(prev)), to: iso(endOfMonth(prev)) },
  ];
}

/** Date range filter: values are YYYY-MM-DD strings ("" = open end). */
export function DateRangeFilter({ from, to, onChange, className, hint = "Filters on each client\u2019s last contact." }: {
  from: string; to: string; onChange: (from: string, to: string) => void; className?: string; hint?: string;
}) {
  const [open, setOpen] = useState(false);
  const f = parse(from), t = parse(to);
  const label = f && t ? `${short(f)} – ${short(t)}` : f ? `From ${short(f)}` : t ? `Until ${short(t)}` : "Last contact";
  const range: DateRange | undefined = f || t ? { from: f, to: t } : undefined;
  const presets = getPresets(new Date());
  const days = f && t ? differenceInCalendarDays(t, f) + 1 : 0;
  const statusText = f && t ? `${days} ${days === 1 ? "day" : "days"} selected` : f ? "Select an end date" : hint;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger render={<Button variant="outline" size="sm" aria-label={`Date range: ${label}`} className={className} />}>
        <CalendarIcon aria-hidden className="h-4 w-4 shrink-0" />{label}
      </PopoverTrigger>
      <PopoverContent align="start" className="w-auto max-w-[calc(100vw-2rem)] gap-0 border border-border/80 p-0 shadow-xl">
        <div className="flex flex-col sm:flex-row">
          <div className="flex flex-wrap gap-1 border-b border-border/70 p-2 sm:w-36 sm:flex-col sm:border-r sm:border-b-0 sm:p-2.5">
            {presets.map((p) => {
              const active = from === p.from && to === p.to;
              return (
                <button key={p.label} type="button"
                  onClick={() => { onChange(p.from, p.to); setOpen(false); }}
                  className={cn(
                    "rounded-md px-2.5 py-1.5 text-left text-xs font-medium transition-colors",
                    active
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:bg-accent hover:text-foreground"
                  )}>
                  {p.label}
                </button>
              );
            })}
          </div>
          <Calendar mode="range" numberOfMonths={2} defaultMonth={f ?? t ?? subMonths(new Date(), 1)} selected={range}
            captionLayout="dropdown" startMonth={new Date(2015, 0)} endMonth={new Date()}
            onSelect={(r) => onChange(iso(r?.from), iso(r?.to))} />
        </div>
        <div className="flex items-center justify-between gap-3 border-t border-border/70 px-3 py-2 text-xs text-muted-foreground">
          <span>{statusText}</span>
          <Button variant="ghost" size="sm" disabled={!from && !to} onClick={() => onChange("", "")}
            className={cn("h-7 border-transparent", range && "hover:text-destructive")}>
            <X className="h-3.5 w-3.5 shrink-0" /> Clear dates
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
