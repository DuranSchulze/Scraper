import { queryOptions } from "@tanstack/react-query";
import type {
  Account, Candidate, CandidateRow, LogDay, LogJob, LogPublished, LogRange, LogsSummary, MailboxBoard, MasterSummary,
  RawItem, Role, RouteKey, Status, TimelineBucket, User,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string, public retryAfter: number | null = null) {
    super(message);
  }
}

/** Tells the other open tabs that someone signed in or out, so they reload instead of showing the old account's data. */
export function announceSessionChange() {
  try { const ch = new BroadcastChannel("scraper-session"); ch.postMessage("changed"); ch.close(); } catch { /* unsupported */ }
}

let signingOut = false;
/** An intentional sign-out: the 401 that follows must not add a "next" page to the login URL. */
export const markSigningOut = (on = true) => { signingOut = on; };

/** Session lost: a full navigation drops every cached record, so the next account never sees stale data. */
function sessionLost() {
  if (typeof window === "undefined" || window.location.pathname === "/login") return;
  const next = signingOut ? "" : window.location.pathname + window.location.search;
  window.location.href = next ? `/login?next=${encodeURIComponent(next)}` : "/login";
}

// The account this page loaded as: pinned from the first /api/auth/me for the life of the document, never replaced.
let pageUser: number | null = null;
// Latched once /me reports another account: stays true until the page reloads, even if /me swings back.
let pageSwitched = false;
export const accountSwitched = () => pageSwitched;
const WRITES = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const SESSION_PATHS = new Set(["/api/auth/login", "/api/auth/logout"]);

/** Calls the FastAPI server through the Next.js /api rewrite. */
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  // Every write names the account that confirmed it; the API refuses it (409) if the cookie now belongs to
  // another account, e.g. one signed in from another tab whose broadcast has not arrived yet.
  const bound = WRITES.has((init.method ?? "GET").toUpperCase()) && !SESSION_PATHS.has(path);
  if (bound && pageSwitched) throw new ApiError(409, "The signed-in account changed in another tab. Nothing was saved; reload the page.");
  if (bound && pageUser === null) throw new ApiError(0, "Your account could not be confirmed yet. Nothing was saved; try again in a moment, or reload the page.");
  const res = await fetch(path, {
    ...init,
    // The API rejects writes without X-Local-GUI (blocks cross-site POSTs).
    headers: { "Content-Type": "application/json", "X-Local-GUI": "1", ...(bound ? { "X-Expected-User": String(pageUser) } : {}), ...init.headers },
  });
  const data = await res.json().catch(() => null);
  // A wrong password is also a 401; the login form shows that inline instead of redirecting.
  if (res.status === 401 && path !== "/api/auth/login") sessionLost();
  if (!res.ok) {
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail
      : Array.isArray(detail) ? detail.map((d: { msg?: string }) => d.msg ?? JSON.stringify(d)).join("; ")
      : res.status >= 500 && !data ? "The scraper API is not reachable. Start it with: uv run python -m scraper.app"
      : res.statusText;
    throw new ApiError(res.status, message, Number(res.headers.get("Retry-After")) || null);
  }
  return data as T;
}

/** Like request(), but also returns X-Total-Count (rows across all pages) and X-Scrape-Job (the scrape "New" refers to). */
async function requestWithTotal<T>(path: string): Promise<{ rows: T; total: number; scrapeJob: number | null }> {
  const res = await fetch(path, { headers: { "X-Local-GUI": "1" } });
  if (!res.ok) {
    // Re-ask through request() for its error message; if that retry succeeds, still fail so the query retries
    // with this function (request() returns the bare array, not { rows, total }).
    await request<unknown>(path);
    throw new ApiError(res.status, res.statusText || "Request failed");
  }
  const rows = (await res.json()) as T;
  return { rows, total: Number(res.headers.get("X-Total-Count") ?? (rows as unknown[]).length),
    scrapeJob: Number(res.headers.get("X-Scrape-Job")) || null };
}

export const post = <T = { ok: boolean }>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });

export type SortKey = "last_contact" | "first_contact";
export type Filters = {
  state: string; stage: string; status: string; department: string; q: string; sort: SortKey; order: "asc" | "desc"; page: number; size: number;
  mailbox: string; service: string; domain: string; has_phone: string; has_amount: string; date_from: string; date_to: string;
  match: string; new: string;
};
/** Filter keys kept in the URL; empty means "no filter" (the API ignores empty values). */
export const FILTER_KEYS = ["stage", "status", "department", "mailbox", "service", "domain", "has_phone", "has_amount", "date_from", "date_to", "match", "new", "q"] as const;
/** Filters tucked into the "More filters" popover; the rest stay on the bar. */
export const MORE_KEYS = ["stage", "status", "department", "mailbox", "service", "domain", "has_phone", "has_amount"] as const;
import type { Bucket } from "./timeline";
export type { Bucket };
export type Facets = { departments: string[]; mailboxes: string[]; services: string[] };
export type BulkResult = { done: number[]; failed: { id: number; error: string }[] };

export const keys = {
  status: ["status"] as const,
  candidates: (f: Filters) => ["candidates", f] as const,
  candidate: (id: number) => ["candidate", id] as const,
  facets: (state: string) => ["facets", state] as const,
  board: ["board"] as const,
  log: (jobId: number | null, mailbox: string) => ["log", jobId, mailbox] as const,
  raw: (id: number) => ["raw", id] as const,
  rules: ["rules"] as const,
  me: ["me"] as const,
  users: ["users"] as const,
  roles: ["roles"] as const,
  schedule: ["schedule"] as const,
  // Under "candidates", so every review action that refreshes the list refreshes the section counts too.
  timeline: (f: Filters, bucket: Bucket, scrapeJob: number | null) => ["candidates", "timeline", f, bucket, scrapeJob] as const,
  // Under "candidates" too: a job ending or a review action refreshes it.
  master: ["candidates", "master-summary"] as const,
};

export type Schedule = { enabled: boolean; interval_minutes: number; last_auto_at: string | null; next_run_at: string | null };

export const scheduleQuery = queryOptions({
  queryKey: keys.schedule,
  queryFn: () => request<Schedule>("/api/schedule"),
  refetchInterval: 30_000,
  retry: false,
});

export const updateSchedule = (body: Pick<Schedule, "enabled" | "interval_minutes">) =>
  request<Schedule>("/api/schedule", { method: "PUT", body: JSON.stringify(body) });

export const statusQuery = queryOptions({
  queryKey: keys.status,
  queryFn: () => request<Status>("/api/status"),
  // Poll fast while a job runs; slower when idle. Keeps polling after errors so the GUI recovers.
  refetchInterval: (q) => (q.state.data?.running ? 2000 : 5000),
  retry: false,
});

/** Query string without empty values: typed API params (e.g. new: bool) reject "". */
const qs = (o: Record<string, string>) => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== ""));

export const candidatesQuery = (f: Filters) => queryOptions({
  queryKey: keys.candidates(f),
  queryFn: () => requestWithTotal<CandidateRow[]>("/api/candidates?" + qs({ ...f, page: String(f.page), size: String(f.size) })),
  placeholderData: (prev) => prev,
});

/** Section counts for the review timeline: same filters as the list, every page, grouped by the sort field's date. */
// Pinned to the list's scrape: a 409 means a newer scrape completed in between, so list and timeline refetch together.
export const timelineQuery = (f: Filters, bucket: Bucket, scrapeJob: number | null) => queryOptions({
  queryKey: keys.timeline(f, bucket, scrapeJob),
  // Page and order don't change section totals; the API ignores them.
  queryFn: () => request<TimelineBucket[]>("/api/candidates/timeline?" + qs({
    ...f, page: String(f.page), size: String(f.size), field: f.sort, bucket, ...(scrapeJob ? { scrape_job: String(scrapeJob) } : {}) })),
  // No placeholder: totals from other filters or another scrape must never sit next to these rows.
  // While loading, sections show their on-page counts.
  retry: (n, e) => !(e instanceof ApiError && e.status === 409) && n < 3,
});

/** Logs page: every query takes the same Philippine-date range. */
const logsPath = (path: string, r: LogRange, extra: Record<string, string> = {}) =>
  `/api/logs/${path}?` + qs({ from: r.from, to: r.to, ...extra });
export const logsSummaryQuery = (r: LogRange) => queryOptions({
  queryKey: ["logs", "summary", r] as const,
  queryFn: () => request<LogsSummary>(logsPath("summary", r)),
  refetchInterval: 30_000,
});
export const logsJobsQuery = (r: LogRange, page: number, size: number) => queryOptions({
  queryKey: ["logs", "jobs", r, page, size] as const,
  queryFn: () => requestWithTotal<LogJob[]>(logsPath("jobs", r, { page: String(page), size: String(size) })),
  placeholderData: (prev) => prev,
  refetchInterval: 30_000,
});
export const logsDailyQuery = (r: LogRange) => queryOptions({
  queryKey: ["logs", "daily", r] as const,
  queryFn: () => request<LogDay[]>(logsPath("daily", r)),
});
export const logsPublishedQuery = (r: LogRange, page: number, size: number) => queryOptions({
  queryKey: ["logs", "published", r, page, size] as const,
  queryFn: () => requestWithTotal<LogPublished[]>(logsPath("published", r, { page: String(page), size: String(size) })),
  placeholderData: (prev) => prev,
});
/** A same-origin download link; the session cookie authorises it. */
export const logsExportUrl = (r: LogRange, table: "jobs" | "daily" | "published" | "all", format: "csv" | "xlsx") =>
  logsPath("export", r, { table, format });

export const masterSummaryQuery = queryOptions({
  queryKey: keys.master,
  queryFn: () => request<MasterSummary>("/api/master/summary"),
  staleTime: 30_000,
});

/** Mailbox progress of the latest scrape job; polled faster while a job runs. */
export const boardQuery = (running: boolean) => queryOptions({
  queryKey: keys.board,
  queryFn: () => request<MailboxBoard>("/api/jobs/latest/mailboxes"),
  refetchInterval: running ? 2000 : 10000,
});

export const logQuery = (jobId: number | null, mailbox: string, running: boolean) => queryOptions({
  queryKey: keys.log(jobId, mailbox),
  queryFn: () => request<{ lines: string[] }>(
    `/api/jobs/${jobId}/log?` + new URLSearchParams({ limit: "2000", ...(mailbox ? { mailbox } : {}) })),
  enabled: jobId != null,
  refetchInterval: running ? 2000 : false,
  placeholderData: (prev) => prev,
});

export const facetsQuery = (state: string) => queryOptions({
  queryKey: keys.facets(state),
  queryFn: () => request<Facets>("/api/candidates/facets?" + new URLSearchParams({ state })),
  staleTime: 60_000,
});

export const candidateQuery = (id: number) => queryOptions({
  queryKey: keys.candidate(id),
  queryFn: () => request<Candidate>(`/api/candidates/${id}`),
});

export const rawQuery = (id: number) => queryOptions({
  queryKey: keys.raw(id),
  queryFn: () => request<RawItem>(`/api/raw/${id}`),
  staleTime: Infinity,
});

export const meQuery = queryOptions({
  queryKey: keys.me,
  queryFn: async () => {
    const me = await request<User>("/api/auth/me");
    // An id past 2^53 would round to a neighbour's id, so it is never pinned and writes stay refused.
    if (pageUser === null && !pageSwitched && Number.isSafeInteger(me.id)) pageUser = me.id;
    else if (pageUser !== null && me.id !== pageUser) pageSwitched = true;
    return me;
  },
  staleTime: 60_000,
  // Page grants can change while signed in; re-read them whenever the tab regains focus.
  refetchOnWindowFocus: "always",
  retry: false,
});

/** Grants that open a page in this app, in nav order; the first granted one is the account's home. */
export const PAGES = [["review", "/"], ["scraping", "/scraping"], ["accounts", "/accounts"]] as const;
/** Fallback home for an account whose grants open no page here. */
export const NO_PAGE = "/no-access";
/** Where an account on a temporary password must go before it may open anything else. */
export const CHANGE_PASSWORD = "/change-password";
// A temporary password opens no page: the API refuses everything but /me, logout and the password change.
export const canUse = (me: User | undefined, route: RouteKey) => !!me && !me.must_change_password && me.routes.includes(route);
export const homeFor = (me: User) => me.must_change_password ? CHANGE_PASSWORD : PAGES.find(([r]) => canUse(me, r))?.[1] ?? NO_PAGE;

export const usersQuery = queryOptions({
  queryKey: keys.users,
  queryFn: () => request<Account[]>("/api/users"),
});
export const resetPassword = (id: number) => post<{ temporary_password: string }>(`/api/users/${id}/reset-password`);
export const changePassword = (current_password: string, new_password: string) =>
  post<User>("/api/auth/password", { current_password, new_password });
export const setUserRole = (id: number, role_id: number) =>
  request<Account>(`/api/users/${id}`, { method: "PATCH", body: JSON.stringify({ role_id }) });

export const rolesQuery = queryOptions({
  queryKey: keys.roles,
  queryFn: () => request<Role[]>("/api/roles"),
});
export const saveRole = (body: { name: string; routes: RouteKey[] }, id?: number) =>
  request<Role>(id ? `/api/roles/${id}` : "/api/roles", { method: id ? "PATCH" : "POST", body: JSON.stringify(body) });
export const deleteRole = (id: number) => request<{ ok: boolean }>(`/api/roles/${id}`, { method: "DELETE" });

export const rulesQuery = queryOptions({
  queryKey: keys.rules,
  queryFn: () => request<{ path: string; text: string }>("/api/rules"),
});
