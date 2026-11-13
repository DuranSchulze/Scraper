import type { JobProgress } from "./types";

/** "12:34", or "1:02:03" past an hour; never negative. */
export function clock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

const STAGE = {
  fetch: { label: "Fetching mail", unit: "mailboxes" },
  extract: { label: "Extracting records", unit: "items" },
  tag: { label: "Tagging against the master list", unit: "" },
} as const;

/** Label, count text and bar value for a running stage. `value` is null while the total is unknown (indeterminate bar). */
export function stageView(p: JobProgress) {
  const stage = STAGE[p.stage];
  const known = p.total > 0;
  // A stage still working never shows a full bar: cap at 99% until the next stage takes over.
  const value = !known ? null : Math.min(99, Math.floor((p.done / p.total) * 100));
  const count = !known || !stage.unit ? "" : `${p.done.toLocaleString("en-US")}/${p.total.toLocaleString("en-US")} ${stage.unit}`;
  return { label: stage.label, count, value };
}
