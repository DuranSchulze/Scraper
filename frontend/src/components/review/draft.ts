import { day, list } from "@/lib/format";
import type { Candidate, ClientIn, ClientStatus, Payment, PaymentBasis, Stage } from "@/lib/types";

export type PayRow = {
  key: string; date: string; origDate: string | null; amount: string; currency: string;
  basis: PaymentBasis; raw_item_id: number | null; kind?: string;
};

/** Everything the reviewer can edit, as form strings. */
export type Draft = {
  name: string; emails: string; phones: string; company: string; department: string;
  stage: Stage; status: ClientStatus; paid: boolean; services: string; notes: string; availed: string;
  payments: PayRow[];
};

let seq = 0;
export const payRow = (p: Partial<Payment>): PayRow => ({
  key: `p${++seq}`, date: day(p.date), origDate: p.date ?? null, amount: p.amount == null ? "" : String(p.amount),
  currency: p.currency ?? "", basis: p.basis ?? "manual", raw_item_id: p.raw_item_id ?? null, kind: p.kind,
});

/** The reviewer's saved edits win over freshly extracted values. */
export function initialDraft(c: Candidate): Draft {
  const r = c.reviewed ?? {}, f = c.fields;
  const v = <K extends keyof ClientIn & keyof typeof f>(k: K) =>
    (r[k] !== undefined ? r[k] : f[k]?.value) as ClientIn[K] | undefined;
  return {
    name: v("name") ?? "", emails: (v("emails") ?? []).join(", "), phones: (v("phones") ?? []).join(", "),
    company: v("company") ?? "", department: v("department") ?? "",
    stage: v("stage") ?? "inquiry", status: v("status") ?? "inquiry", paid: !!v("engagement_paid"),
    services: (v("services") ?? []).join(", "), notes: r.notes ?? "",
    availed: (v("services_availed") ?? []).join(", "), payments: (v("payments") ?? []).map(payRow),
  };
}

export function readPayments(rows: PayRow[]): Payment[] {
  return rows.map((r) => {
    const amount = r.amount === "" ? null : Number(r.amount);
    return {
      // Keep the original timestamp when the reviewer did not change the day.
      date: r.date ? (r.date === day(r.origDate) ? r.origDate : r.date) : null,
      amount, currency: r.currency.trim().toUpperCase() || null,
      basis: amount === null ? "unknown" : r.basis === "unknown" ? "manual" : r.basis,
      raw_item_id: r.raw_item_id,
    };
  });
}

/** Returns an error message, or null when the draft can be approved. */
export function validate(d: Draft): string | null {
  const pays = readPayments(d.payments);
  const bad = pays.findIndex((p) => (p.amount === null) !== (p.currency === null)
    || (p.currency !== null && !/^[A-Z]{3}$/.test(p.currency)) || (p.amount !== null && !(p.amount > 0)));
  if (bad >= 0) return `Payment ${bad + 1}: enter both an amount above 0 and a 3-letter currency, or leave both empty.`;
  if (pays.length && !d.paid) return 'Tick "Engagement paid", or remove the payment rows.';
  return null;
}

export function toClientIn(d: Draft, c: Candidate): ClientIn {
  const r = c.reviewed ?? {}, f = c.fields;
  return {
    name: d.name.trim() || null, emails: list(d.emails), phones: list(d.phones), company: d.company.trim() || null,
    department: d.department.trim() || null, stage: d.stage, status: d.status, engagement_paid: d.paid,
    services: list(d.services), notes: d.notes.trim() || null, services_availed: list(d.availed),
    payments: readPayments(d.payments),
    amounts: r.amounts ?? f.amounts?.value ?? [], handlers: r.handlers ?? f.handlers?.value ?? [],
    first_contact: r.first_contact ?? f.first_contact?.value ?? null,
    last_contact: r.last_contact ?? f.last_contact?.value ?? null,
  };
}

// --- Unsaved edits survive reloads and restarts (browser storage) ---
// Stored with the record's email_key: IDs restart after a database reset, so a draft for
// another person under the same ID is discarded instead of restored.

type Stored = { email_key: string; saved_at: string; draft: Draft };
const storageKey = (id: number) => `scraper:draft:${id}`;

export function loadDraft(c: Candidate): Stored | null {
  try {
    const s = JSON.parse(localStorage.getItem(storageKey(c.id)) ?? "null") as Stored | null;
    if (s && s.email_key === c.email_key) return s;
    if (s) localStorage.removeItem(storageKey(c.id));
  } catch { /* storage blocked or corrupt: start from the record */ }
  return null;
}

export function saveDraft(c: Candidate, draft: Draft) {
  try {
    localStorage.setItem(storageKey(c.id), JSON.stringify({ email_key: c.email_key, saved_at: new Date().toISOString(), draft }));
  } catch { /* storage full or blocked: edits still work, just not restored */ }
}

export function clearDraft(id: number) {
  try { localStorage.removeItem(storageKey(id)); } catch { /* ignore */ }
}
