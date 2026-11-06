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
