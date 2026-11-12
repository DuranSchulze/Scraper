// Review timeline grouping. Pure and import-free (type imports only) so `node --test` can run timeline.test.mjs directly.
import type { CandidateRow } from "./types";

export type Bucket = "day" | "week";
export type DateField = "last_contact" | "first_contact";

/** The timeline section a date falls in: its day, or the Monday of its ISO week; null for a missing or invalid date (matches the API). */
export function bucketOf(value: string | null, bucket: Bucket): string | null {
  const d = value?.slice(0, 10) ?? "";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return null;
  const t = new Date(`${d}T00:00:00Z`);
  if (Number.isNaN(t.getTime()) || t.toISOString().slice(0, 10) !== d) return null;
  if (bucket === "week") t.setUTCDate(t.getUTCDate() - ((t.getUTCDay() + 6) % 7));
  return t.toISOString().slice(0, 10);
}

/**
 * Page rows grouped into one section per bucket, in the order buckets first appear (the list's sort order),
 * with missing or invalid dates gathered into one "No date" section at the end, as the API's timeline orders them.
 */
export function sections<R extends Pick<CandidateRow, DateField>>(rows: R[], field: DateField, bucket: Bucket) {
  const groups = new Map<string | null, R[]>();
  for (const r of rows) {
    const key = bucketOf(r[field], bucket);
    const list = groups.get(key);
    if (list) list.push(r); else groups.set(key, [r]);
  }
  const out = [...groups].filter(([key]) => key !== null).map(([key, list]) => ({ key, rows: list }));
  const undated = groups.get(null);
  if (undated) out.push({ key: null, rows: undated });
  return out;
}
