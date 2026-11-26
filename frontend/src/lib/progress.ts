import type { JobProgress } from "./types";

/** "12:34", or "1:02:03" past an hour; never negative. */
export function clock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

/** Explicit unit labels ("1h 02m 03s", "12m 34s", "45s"); never negative. */
export function clockLabeled(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  if (h) return `${h}h ${pad(m)}m ${pad(sec)}s`;
  if (m) return `${m}m ${pad(sec)}s`;
  return `${sec}s`;
}

const STAGE = {
  fetch: { label: "Fetching mail", unit: "mailboxes" },
  extract: { label: "Extracting records", unit: "items" },
  tag: { label: "Tagging against the master list", unit: "" },
} as const;

/** Label, count text and bar value for a running stage. `value` is null while the total is unknown (indeterminate bar). */
export function stageView(p: JobProgress) {
  // The backlog pass runs after this run's own items; its count moves while done/total stay fixed.
  if (p.stage === "extract" && p.backlog_total) {
    const value = Math.min(99, Math.floor(((p.backlog_done ?? 0) / p.backlog_total) * 100));
    const full = (p.backlog_done ?? 0) >= p.backlog_total;
    return { label: full ? "Finishing up" : "Extracting older backlog",
      count: `${(p.backlog_done ?? 0).toLocaleString("en-US")}/${p.backlog_total.toLocaleString("en-US")} backlog items`, value };
  }
  const stage = STAGE[p.stage];
  const known = p.total > 0;
  // A stage still working never shows a full bar: cap at 99% until the next stage takes over.
  const value = !known ? null : Math.min(99, Math.floor((p.done / p.total) * 100));
  const count = !known || !stage.unit ? "" : `${p.done.toLocaleString("en-US")}/${p.total.toLocaleString("en-US")} ${stage.unit}`;
  // Every item counted but the job still runs (backlog, tagging, saving): say so, so a full count isn't read as done.
  if (known && p.done >= p.total) return { label: "Finishing up", count, value };
  return { label: stage.label, count, value };
}
