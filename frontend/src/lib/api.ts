import { queryOptions } from "@tanstack/react-query";
import type { Candidate, CandidateRow, MailboxBoard, RawItem, Status } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** Calls the FastAPI server through the Next.js /api rewrite. */
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...init,
    // The API rejects writes without this header (blocks cross-site POSTs).
    headers: { "Content-Type": "application/json", "X-Local-GUI": "1", ...init.headers },
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail
      : Array.isArray(detail) ? detail.map((d: { msg?: string }) => d.msg ?? JSON.stringify(d)).join("; ")
      : res.status >= 500 && !data ? "The scraper API is not reachable. Start it with: uv run python -m scraper.app"
      : res.statusText;
    throw new ApiError(res.status, message);
  }
  return data as T;
}

/** Like request(), but also returns the X-Total-Count header (number of rows before the 500 cap). */
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
  state: string; stage: string; status: string; department: string; q: string; sort: SortKey; order: "asc" | "desc";
};

export const keys = {
  status: ["status"] as const,
  candidates: (f: Filters) => ["candidates", f] as const,
  candidate: (id: number) => ["candidate", id] as const,
  board: ["board"] as const,
  log: (jobId: number | null, mailbox: string) => ["log", jobId, mailbox] as const,
  raw: (id: number) => ["raw", id] as const,
  rules: ["rules"] as const,
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
  queryFn: () => requestWithTotal<CandidateRow[]>("/api/candidates?" + new URLSearchParams(f)),
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

export const candidateQuery = (id: number) => queryOptions({
  queryKey: keys.candidate(id),
  queryFn: () => request<Candidate>(`/api/candidates/${id}`),
});

export const rawQuery = (id: number) => queryOptions({
  queryKey: keys.raw(id),
  queryFn: () => request<RawItem>(`/api/raw/${id}`),
  staleTime: Infinity,
});

export const rulesQuery = queryOptions({
  queryKey: keys.rules,
  queryFn: () => request<{ path: string; text: string }>("/api/rules"),
});
