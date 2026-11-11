import { queryOptions } from "@tanstack/react-query";
import { ROUTES, type Account, type Candidate, type CandidateRow, type MailboxBoard, type RawItem, type RouteKey, type Status, type User } from "./types";

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

/** Calls the FastAPI server through the Next.js /api rewrite. */
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...init,
    // The API rejects writes without this header (blocks cross-site POSTs).
    headers: { "Content-Type": "application/json", "X-Local-GUI": "1", ...init.headers },
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

/** Like request(), but also returns the X-Total-Count header (number of rows across all pages). */
async function requestWithTotal<T>(path: string): Promise<{ rows: T; total: number }> {
  const res = await fetch(path, { headers: { "X-Local-GUI": "1" } });
  if (!res.ok) return request<{ rows: T; total: number }>(path); // reuse request()'s error message
  const rows = (await res.json()) as T;
  return { rows, total: Number(res.headers.get("X-Total-Count") ?? (rows as unknown[]).length) };
}

export const post = <T = { ok: boolean }>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });

export type SortKey = "last_contact" | "first_contact";
export type Filters = {
  state: string; stage: string; status: string; department: string; q: string; sort: SortKey; order: "asc" | "desc"; page: number;
  mailbox: string; service: string; domain: string; has_phone: string; has_amount: string; date_from: string; date_to: string;
};
/** Filter keys kept in the URL; empty means "no filter" (the API ignores empty values). */
export const FILTER_KEYS = ["stage", "status", "department", "mailbox", "service", "domain", "has_phone", "has_amount", "date_from", "date_to", "q"] as const;
export type Facets = { departments: string[]; mailboxes: string[]; services: string[] };
export type BulkResult = { done: number[]; failed: { id: number; error: string }[] };
export const PAGE_SIZE = 50;

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
};

export const statusQuery = queryOptions({
  queryKey: keys.status,
  queryFn: () => request<Status>("/api/status"),
  // Poll fast while a job runs; slower when idle. Keeps polling after errors so the GUI recovers.
  refetchInterval: (q) => (q.state.data?.running ? 2000 : 5000),
  retry: false,
});

export const candidatesQuery = (f: Filters) => queryOptions({
  queryKey: keys.candidates(f),
  queryFn: () => requestWithTotal<CandidateRow[]>("/api/candidates?" + new URLSearchParams({ ...f, page: String(f.page), size: String(PAGE_SIZE) })),
  placeholderData: (prev) => prev,
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
  queryFn: () => request<User>("/api/auth/me"),
  staleTime: 60_000,
  // Page grants can change while signed in; re-read them whenever the tab regains focus.
  refetchOnWindowFocus: "always",
  retry: false,
});

/** Where each grantable page lives; the first granted one (ROUTES order) is a member's home. */
export const ROUTE_PATH: Record<RouteKey, string> = { review: "/", scraping: "/scraping" };
export const canUse = (me: User | undefined, route: RouteKey) => !!me && (me.role === "admin" || me.routes.includes(route));
export const homeFor = (me: User) => ROUTE_PATH[ROUTES.find((r) => canUse(me, r)) ?? "review"];

export const updateRoutes = (id: number, routes: RouteKey[]) =>
  request<Account>(`/api/users/${id}`, { method: "PATCH", body: JSON.stringify({ routes }) });

export const usersQuery = queryOptions({
  queryKey: keys.users,
  queryFn: () => request<Account[]>("/api/users"),
});

export const rulesQuery = queryOptions({
  queryKey: keys.rules,
  queryFn: () => request<{ path: string; text: string }>("/api/rules"),
});
