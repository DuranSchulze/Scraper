// Shapes returned by the FastAPI server (scraper/app.py). Client-safe: no server imports.

export const STAGES = ["inquiry", "quoted", "invoiced", "paid"] as const;
export type Stage = (typeof STAGES)[number];
export const STATUSES = ["inquiry", "active", "past", "lost"] as const;
export type ClientStatus = (typeof STATUSES)[number];
export const REVIEW_STATES = ["pending", "approved", "rejected", "merged"] as const;
export type ReviewState = (typeof REVIEW_STATES)[number];

export type JobStatus = "running" | "done" | "done_with_errors" | "failed" | "interrupted";

export type Status = {
  running: boolean;
  job: string;
  error: string | null;
  log: string[];
  candidates: Partial<Record<ReviewState, number>>;
  raw_items: Partial<Record<"gmail" | "contacts" | "sheets", number>>;
  ready_to_publish: number;
  extract_failed: number;
  job_id: number | null;
  job_status: JobStatus | null;
  job_params: Record<string, unknown>;
  job_failures: number;
  job_started_at: string | null;
  job_finished_at: string | null;
};

export type CandidateRow = {
  id: number;
  email_key: string;
  review_state: ReviewState;
  name: string | null;
  status: string | null;
  department: string | null;
  paid: boolean;
  stage: Stage;
  needs_check: number;
  last_contact: string | null;
};

export type Field<T> = { value: T; source: string; confidence: number };

export type Amount = { amount: number; currency: string; total?: boolean };
export type StageAmount = { stage: "quoted" | "invoiced"; amount: number; currency: string; date: string | null; raw_item_id: number };
export type PaymentBasis = "stated" | "invoice" | "unknown" | "manual";
export type Payment = {
  date: string | null;
  amount: number | null;
  currency: string | null;
  basis: PaymentBasis;
  raw_item_id: number | null;
  kind?: "confirmed" | "claim" | "screenshot";
};
export type Handler = { department: string; mailbox: string; first: string | null; last: string | null; messages: number };
export type TimelineItem = {
  date: string | null; stage: string; department: string; mailbox: string;
  amounts: Amount[]; raw_item_id: number; snippet: string;
};
export type Evidence = {
  source: string; raw_item_id: number; direction: "in" | "out"; date: string | null; snippet: string;
  stage: string | null; amounts: Amount[]; reasons: string[]; services: string[];
};

export type CandidateFields = Partial<{
  name: Field<string>; emails: Field<string[]>; phones: Field<string[]>; company: Field<string>;
  stage: Field<Stage>; status: Field<ClientStatus>; services: Field<string[]>; department: Field<string>;
  engagement_paid: Field<boolean>; amounts: Field<StageAmount[]>; stage_dates: Field<Partial<Record<Stage, string>>>;
  needs_check: Field<string[]>; first_contact: Field<string>; last_contact: Field<string>; sources: Field<string[]>;
  payments: Field<Payment[]>; client_since: Field<string>; services_availed: Field<string[]>;
  handlers: Field<Handler[]>; timeline: Field<{ items: TimelineItem[]; truncated: boolean }>;
}>;

/** Body of POST /api/candidates/{id}/approve, also what `reviewed` holds. */
export type ClientIn = {
  name: string | null; emails: string[]; phones: string[]; company: string | null;
  status: ClientStatus; services: string[]; department: string | null; engagement_paid: boolean;
  stage: Stage; amounts: StageAmount[]; payments: Payment[]; services_availed: string[]; handlers: Handler[];
  first_contact: string | null; last_contact: string | null; notes: string | null; client_since?: string | null;
};

export type Candidate = {
  id: number; email_key: string; review_state: ReviewState; fields: CandidateFields; evidence: Evidence[];
  merged_into: number | null; client_id: number | null; reviewed: Partial<ClientIn> | null;
  reviewed_at: string | null; published_at: string | null;
};

export type RawItem = {
  id: number; source: string; mailbox: string; source_id: string;
  payload: {
    headers?: Record<string, string>; body?: string;
    attachments?: { filename: string }[]; attachment_text?: Record<string, string>;
  } & Record<string, unknown>;
};
