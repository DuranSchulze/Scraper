"use client";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";

export type Option = { value: string; label: string };

/** Single picker. "" is a real option value (e.g. "Any stage"), mapped to a sentinel for Base UI. */
export function Pick({ value, onChange, options, label, id, className, disabled }: {
  value: string; onChange: (v: string) => void; options: Option[]; label: string; id?: string; className?: string; disabled?: boolean;
}) {
  const ANY = "__any__";
  const items = options.map((o) => ({ value: o.value || ANY, label: o.label }));
  return (
    <Select value={value || ANY} items={items} disabled={disabled} onValueChange={(v) => onChange(v === ANY ? "" : String(v))}>
      <SelectTrigger id={id} aria-label={label} className={cn("w-full", className)}>
        <SelectValue />
      </SelectTrigger>
      {/* Open below the trigger, not over it, so neighbouring filters stay visible. */}
      {/* Size the list to its options (at least the trigger, at most ~30rem) so long values stay readable; longer ones wrap. */}
      <SelectContent alignItemWithTrigger={false} align="start" className="w-max min-w-(--anchor-width) max-w-[min(30rem,calc(100vw-2rem))]">
        {items.map((o) => <SelectItem key={o.value} value={o.value} className="*:break-words *:whitespace-normal">{o.label}</SelectItem>)}
      </SelectContent>
    </Select>
  );
}
