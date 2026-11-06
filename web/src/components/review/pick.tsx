"use client";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";

export type Option = { value: string; label: string };

/** Single picker. "" is a real option value (e.g. "Any stage"), mapped to a sentinel for Base UI. */
export function Pick({ value, onChange, options, label, id, className }: {
  value: string; onChange: (v: string) => void; options: Option[]; label: string; id?: string; className?: string;
}) {
  const ANY = "__any__";
  const items = options.map((o) => ({ value: o.value || ANY, label: o.label }));
  return (
    <Select value={value || ANY} items={items} onValueChange={(v) => onChange(v === ANY ? "" : String(v))}>
      <SelectTrigger id={id} aria-label={label} className={cn("w-full", className)}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {items.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
      </SelectContent>
    </Select>
  );
}
