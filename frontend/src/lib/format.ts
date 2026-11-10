export const day = (iso?: string | null) => (iso ?? "").slice(0, 10);

export const stamp = (iso?: string | null) => (iso ? iso.slice(0, 16).replace("T", " ") : "");

export const money = (a: { amount: number; currency: string }) =>
  `${a.currency} ${Number(a.amount).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;

export const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

/** Whole months between two dates, as "1 yr 3 mo" / "4 mo" / "under 1 mo". */
export function tenure(fromIso: string, toIso?: string | null): string {
  const from = new Date(fromIso);
  const to = toIso ? new Date(toIso) : new Date();
  const months = (to.getFullYear() - from.getFullYear()) * 12 + to.getMonth() - from.getMonth()
    - (to.getDate() < from.getDate() ? 1 : 0);
  if (months < 1) return "under 1 mo";
  const y = Math.floor(months / 12), m = months % 12;
  return [y && `${y} yr`, m && `${m} mo`].filter(Boolean).join(" ");
}

/** "3 days ago" style age of an ISO timestamp; "" when unknown. */
export function ago(iso?: string | null, now = Date.now()): string {
  const t = iso ? Date.parse(iso) : NaN;
  if (!isFinite(t)) return "";
  const m = Math.max(0, Math.round((now - t) / 60000));
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  return d < 60 ? `${d} day${d === 1 ? "" : "s"} ago` : `${Math.round(d / 30)} months ago`;
}
