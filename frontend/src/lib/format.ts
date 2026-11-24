/** Every time in the app is shown in Philippine time, whatever the viewer's computer is set to. */
export const PH = "Asia/Manila";

const PH_PARTS = new Intl.DateTimeFormat("en-CA", {
  timeZone: PH, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
const PH_LONG = new Intl.DateTimeFormat("en-US", { timeZone: PH, dateStyle: "medium", timeStyle: "short" });

/** "YYYY-MM-DD HH:MM" in Philippine time; "" for empty or unparseable input. */
function phParts(iso: string) {
  const t = Date.parse(iso);
  if (!isFinite(t)) return null;
  const p = Object.fromEntries(PH_PARTS.formatToParts(t).map((x) => [x.type, x.value]));
  return { date: `${p.year}-${p.month}-${p.day}`, time: `${p.hour}:${p.minute}` };
}

/** The Philippine calendar date of an instant. A plain date ("2026-11-15") is already a calendar day and is kept. */
export const day = (iso?: string | null) => !iso ? "" : /^\d{4}-\d{2}-\d{2}$/.test(iso) ? iso : phParts(iso)?.date ?? iso.slice(0, 10);

export const stamp = (iso?: string | null) => {
  const p = iso ? phParts(iso) : null;
  return p ? `${p.date} ${p.time}` : "";
};

/** "Nov 24, 2026, 9:23 AM PHT"; "" when unknown. */
export const phDateTime = (iso?: string | number | null) => {
  const t = typeof iso === "number" ? iso : iso ? Date.parse(iso) : NaN;
  return isFinite(t) ? `${PH_LONG.format(t)} PHT` : "";
};

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
