"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";

/** Rows-per-page choices, shared with the Client Directory app. */
export const PAGE_SIZES = [10, 25, 50, 100] as const;
export const DEFAULT_PAGE_SIZE = 25;
/** A URL or state value as a page size; anything not offered falls back to the default. */
export const pageSize = (v: unknown) => (PAGE_SIZES as readonly number[]).includes(Number(v)) ? Number(v) : DEFAULT_PAGE_SIZE;

/** The Client Directory's pager: "Showing X to Y of N", a rows-per-page select, and previous / page of pages / next.
 *  While `loading`, the total may be stale: the range is hidden and paging is blocked, but the buttons stay focusable
 *  (aria-disabled, not disabled, so keyboard focus survives the request). */
export function TablePager({ page, size, total, loading = false, itemLabel = "rows", onPage, onSize }: {
  page: number; size: number; total: number; loading?: boolean; itemLabel?: string;
  onPage: (p: number) => void; onSize: (s: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / size));
  const start = (page - 1) * size;
  // Server pages are full except the last.
  const shown = Math.max(0, Math.min(size, total - start));
  const canPrev = !loading && page > 1, canNext = !loading && page < pages;
  return (
    <div data-print="hide" aria-busy={loading} className="flex flex-wrap items-center justify-between gap-3 px-5 pt-3 pb-4 text-xs text-muted-foreground">
      <span className="hidden tabular-nums sm:inline" aria-live="polite">
        {loading ? "Loading…" : `Showing ${(shown ? start + 1 : 0).toLocaleString()} to ${(start + shown).toLocaleString()} of ${total.toLocaleString()} ${itemLabel}`}
      </span>
      <div className="flex items-center gap-2 max-sm:w-full max-sm:justify-between">
        <label className="flex items-center gap-2">Rows
          <select value={size} onChange={(e) => onSize(Number(e.target.value))}
            className="h-8 cursor-pointer rounded-md border border-border bg-muted/40 px-2 text-xs text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring">
            {PAGE_SIZES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <span className="flex items-center gap-1">
          <Button variant="ghost" size="icon" aria-label="Previous page" aria-disabled={!canPrev} onClick={() => canPrev && onPage(page - 1)}
            className="aria-disabled:pointer-events-none aria-disabled:opacity-50"><ChevronLeft /></Button>
          <span className="min-w-14 text-center tabular-nums">{loading ? "…" : `${page.toLocaleString()} / ${pages.toLocaleString()}`}</span>
          <Button variant="ghost" size="icon" aria-label="Next page" aria-disabled={!canNext} onClick={() => canNext && onPage(page + 1)}
            className="aria-disabled:pointer-events-none aria-disabled:opacity-50"><ChevronRight /></Button>
        </span>
      </div>
    </div>
  );
}
