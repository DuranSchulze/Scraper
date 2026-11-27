// Shapes returned by the FastAPI server (scraper/app.py). Client-safe: no server imports.

export const STAGES = ["inquiry", "quoted", "invoiced", "paid"] as const;
export type Stage = (typeof STAGES)[number];
export const STATUSES = ["inquiry", "active", "past", "lost"] as const;
export type ClientStatus = (typeof STATUSES)[number];
export const REVIEW_STATES = ["pending", "approved", "rejected", "merged"] as const;
export type ReviewState = (typeof REVIEW_STATES)[number];

export type JobStatus = "running" | "paused" | "done" | "done_with_errors" | "failed" | "interrupted" | "stopped";

export type Status = {
  running: boolean;
  job: string;
  /** Omitted for members without the Scraping page (scrape details are restricted). */
  error?: string | null;
  log?: string[];
  candidates: Partial<Record<ReviewState, number>>;
  raw_items: Partial<Record<"gmail" | "contacts" | "sheets", number>>;
  ready_to_publish: number;
  extract_failed: number;
  job_id: number | null;
  job_status: JobStatus | null;
  /** A pause/stop request the job has not reacted to yet (it acts after the current batch). */
  job_control: "pause" | "stop" | null;
  job_params?: Record<string, unknown>;
  job_failures: number;
  job_started_at: string | null;
  job_finished_at: string | null;
  /** Stage of the running or paused job; null when idle or without the Scraping page. */
  job_progress: JobProgress | null;
  /** Latest scrape that genuinely completed; "New" records belong to it. */
  last_complete_scrape_job: number | null;
  last_complete_scrape_at: string | null;
};

/** Logs page (GET /api/logs/*). Dates are Philippine calendar days; timestamps are ISO instants. */
export type LogRange = { from: string; to: string };
export type LogsSummary = {
  range: LogRange;
  stored: Partial<Record<"gmail" | "contacts" | "sheets", number>>;
  fetched: Partial<Record<"gmail" | "contacts" | "sheets", number>>;
  runs: { total: number; auto: number; full: number; completed: number; unfinished: number };
  last_complete_scrape_at: string | null;
  last_full_scrape_at: string | null;
  next_run_at: string | null;
  schedule: { enabled: boolean | null; interval_minutes: number | null };
  backlog_pending: number;
  published: number;
  clients_total: number;
  candidates: Partial<Record<ReviewState, number>>;
};
export type LogJob = {
  id: number; name: "scrape" | "extract"; status: JobStatus; completed: boolean; auto: boolean; full: boolean;
  started_at: string; finished_at: string | null; seconds: number | null; failures: number; error: string | null;
  mailboxes_done: number; mailboxes: number; emails_fetched: number;
};
export type LogDay = { day: string; emails: number; contacts: number; sheet_rows: number; runs: number; completed_runs: number; published: number };
export type LogPublished = {
  id: number; email: string; name: string | null; company: string | null; client_id: number | null;
  first_published_at: string; published_at: string | null; reviewed_by: string | null;
};

export type JobProgress = {
  stage: "fetch" | "extract" | "tag"; done: number; total: number; started_at: string;
  /** Present while the time-boxed backlog pass drains older unextracted items. */
  backlog_done?: number; backlog_total?: number;
};

/** Client Directory master list (shared companies + contacts tables) and the pending tags made from it. */
export type MasterSummary = {
  available: boolean; companies: number; contacts: number;
  email: number; domain: number; none: number; approvable: number; tagged_at: string | null;
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
  first_contact: string | null;
  /** Distinct scraped items (emails, contacts, sheet rows) behind this record, incl. merged ones. */
  items: number;
  company: string | null;
  phones: string[];
  /** Services availed, or the services mentioned when none are confirmed yet. */
  services: string[];
  latest_amount: { amount: number; currency: string; stage: string; date: string | null } | null;
  item_sources: { gmail: number; contacts: number; sheets: number };
  /** Client Directory tag (pending records only): a contact has this email, or this company domain. */
  master_match: MasterMatch | null;
  /** Directory company names the tag points at. */
  master_names: string[];
  /** Mailboxes this record was found in, busiest first. */
  mailboxes: string[];
  /** Got new or changed data in the latest completed scrape. */
  is_new: boolean;
  /** When the scrape that last brought new data for this record finished. */
  last_scraped_at: string | null;
};

export type MailboxState = "pending" | "running" | "done" | "failed" | "skipped";
export type MailboxProgress = {
  mailbox: string; status: MailboxState; fetched: number; total: number; rate_limited: number;
  error: string | null; started_at: string | null; finished_at: string | null; last_synced: string | null;
};
export type MailboxBoard = {
  job_id: number | null; items: MailboxProgress[]; counts: Record<MailboxState, number>; synced_before: number;
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

/** Grants a role can hold, in the API's canonical order. */
export const ROUTES = ["review", "scraping", "accounts"] as const;
export type RouteKey = (typeof ROUTES)[number];
export type RoleRef = { id: number; name: string };
/** A custom role: the pages its accounts may use. The locked Owner role always has every grant. */
export type Role = RoleRef & { routes: RouteKey[]; locked: boolean; users: number };
/** `routes` come from the account's role, re-read by the API on every request. */
export type User = { id: number; email: string; name: string; role: RoleRef; routes: RouteKey[]; must_change_password: boolean; session_version: number };
export type Account = User & { active: boolean; created_at: string };

/** "none": a pending record checked against the directory with no match. null: not checked, or not pending. */
export type MasterMatch = "email" | "domain" | "none";
/** One section of the review timeline: counts across every page for one day (or ISO week, keyed by its Monday). */
export type TimelineBucket = { bucket: string | null; total: number; email: number; domain: number };
