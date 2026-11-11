"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pause, Play, Square, Upload } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { boardQuery, canUse, keys, meQuery, post, statusQuery } from "@/lib/api";
import { stamp } from "@/lib/format";
import type { RouteKey, Status, User } from "@/lib/types";
import { Pill } from "@/components/review/badges";
import { ConfirmDialog } from "@/components/review/confirm-dialog";
import { LoadingLabel, Skeleton } from "@/components/ui/skeleton";

const JOB_STATE = {
  running: { label: "Running", tone: "info" },
  paused: { label: "Paused", tone: "warning" },
  stopped: { label: "Stopped", tone: "neutral" },
  done: { label: "Finished", tone: "success" },
  done_with_errors: { label: "Finished with errors", tone: "warning" },
  failed: { label: "Failed", tone: "danger" },
  interrupted: { label: "Interrupted", tone: "warning" },
} as const;

/** Slim bar shown on both tabs: current job, its controls, mailbox progress and Publish. */
export function StatusBar({ leading }: { leading?: React.ReactNode }) {
  const qc = useQueryClient();
  const { data: s, error } = useQuery(statusQuery);
  const { data: me } = useQuery(meQuery);
  // Scrape controls/progress need the Scraping page, Publish needs Review; unknown grants show (and fetch) neither.
  const canScrape = canUse(me, "scraping");
  const canReview = canUse(me, "review");
  const running = !!s?.running;
  const { data: board } = useQuery({ ...boardQuery(running), enabled: canScrape });
  const [confirm, setConfirm] = useState<null | "stop" | "publish">(null);

  // Records, the board and the log change while a job runs; refresh them on each poll and once when it ends.
  useEffect(() => {
    qc.invalidateQueries({ queryKey: ["candidates"] });
    qc.invalidateQueries({ queryKey: keys.board });
    qc.invalidateQueries({ queryKey: ["log"] });
  }, [running, s?.job_status, s?.log?.length, qc]);

  // Drop a pending confirmation whose page grant was just revoked, so it can't reopen later (adjusting state while rendering).
  if ((confirm === "stop" && !canScrape) || (confirm === "publish" && !canReview)) setConfirm(null);

  // Confirm handlers check the freshest grants in the cache, not this render's (a revoke may not have painted yet).
  const allowedNow = (route: RouteKey) => canUse(qc.getQueryData<User>(keys.me), route);
  const refresh = () => qc.invalidateQueries({ queryKey: keys.status });
  const fail = (e: Error) => toast.error(e.message);
  const jobId = s?.job_id ?? null;
  const control = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "pause" | "continue" | "stop" }) => post(`/api/jobs/${id}/${action}`),
    onSuccess: (_r, v) => { if (v.action === "stop") setConfirm(null); refresh(); },
    onError: (e: Error) => { fail(e); refresh(); },
  });
  const resume = useMutation({
    mutationFn: (id: number) => post(`/api/jobs/${id}/resume`),
    onSuccess: () => { toast.success("Resumed"); refresh(); }, onError: fail,
  });
  const publish = useMutation({
    mutationFn: () => post<{ published: number; inserted: number; updated: number }>("/api/publish"),
    onSuccess: (r) => {
      setConfirm(null);
      toast.success(`Published ${r.published}: ${r.inserted} new, ${r.updated} updated.`);
      qc.invalidateQueries();
    },
    onError: fail,
  });

  const state = s?.job_status ? JOB_STATE[s.job_status] : null;
  const ready = s?.ready_to_publish ?? 0;
  const counts = board?.counts;
  const total = board ? board.items.length : 0;
  const finished = counts ? counts.done + counts.skipped : 0;
  const note = !canScrape ? null : s?.job_control === "stop" ? "Stopping after the current batch is saved…"
    : s?.job_control === "pause" && s.job_status === "running" ? "Pausing after the current batch is saved…"
    : s?.job_status === "paused" && !s.job_control ? "Continuing…"
    : s?.job_status === "interrupted" ? "The server stopped while this job ran. Resume continues it; fetched mail is skipped."
    : s?.job_status === "stopped" ? "Stopped. Everything saved so far is kept; Resume continues from here."
    : s?.job_status === "failed" ? `${s.error ?? "The job failed."} Resume continues from where it stopped; finished work is kept.` : null;

  return (
    <header className="sticky top-0 z-30 border-b bg-card">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 lg:px-6">
        {leading}

        <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
          {!s && !error ? <><LoadingLabel>Loading job status…</LoadingLabel><Skeleton className="h-4 w-32" /><Skeleton className="h-5 w-20 rounded-full" /></>
            : s?.job ? <span className="text-muted-foreground">Last job: <span className="font-medium text-foreground">{s.job}</span></span>
            : <span className="text-muted-foreground">No job has run yet</span>}
          {state && <Pill tone={state.tone}>{state.label}{s?.job_failures ? ` · ${s.job_failures} failure(s)` : ""}</Pill>}
          {s?.job_started_at && <span className="text-xs text-muted-foreground tabular-nums">started {stamp(s.job_started_at)}</span>}
          {jobId && canScrape && <JobControls status={s!.job_status} control={s!.job_control} busy={control.isPending || resume.isPending}
            resuming={resume.isPending}
            onPause={() => control.mutate({ id: jobId, action: "pause" })}
            onContinue={() => control.mutate({ id: jobId, action: "continue" })}
            onStop={() => setConfirm("stop")}
            onResume={() => resume.mutate(jobId)} />}
        </div>

        {canScrape && total > 0 && (
          <div className="flex min-w-[12rem] flex-1 items-center gap-2 sm:max-w-xs" title="Mailboxes finished in the latest scrape">
            <Progress value={(finished / total) * 100} aria-label="Mailboxes finished" className="flex-1" />
            <span className="text-xs whitespace-nowrap text-muted-foreground tabular-nums">
              {finished}/{total} mailboxes{counts?.running ? ` · ${counts.running} running` : ""}
            </span>
          </div>
        )}

        {canReview && (
          <Button size="sm" className="ml-auto" disabled={!ready || publish.isPending} onClick={() => setConfirm("publish")}>
            <Upload className="h-4 w-4 shrink-0" /> Publish {ready} approved
          </Button>
        )}
      </div>

      {(note || error || (canScrape && !!s?.extract_failed)) && (
        <div className="flex flex-col gap-1 border-t px-4 py-2 text-xs lg:px-6">
          {error && <p role="alert" className="text-destructive">{error.message} Retrying every few seconds…</p>}
          {note && <p role="status" className={s?.job_status === "failed" ? "break-words text-destructive" : "text-muted-foreground"}>{note}</p>}
          {canScrape && !!s?.extract_failed && <p className="font-medium text-warning">{s.extract_failed} item(s) failed extraction and will be retried on the next run.</p>}
        </div>
      )}

      {/* Each dialog is tied to its grant too: a revoked page closes it and its confirm does nothing. */}
      <ConfirmDialog open={confirm === "stop" && canScrape} onOpenChange={(o) => !o && setConfirm(null)}
        title={`Stop the ${s?.job || "job"}?`} confirmLabel="Stop" pending={control.isPending}
        description="It stops after the current batch is saved. Everything saved so far is kept, and Resume continues from where it stopped."
        onConfirm={() => allowedNow("scraping") && jobId && control.mutate({ id: jobId, action: "stop" })} />
      <ConfirmDialog open={confirm === "publish" && canReview} onOpenChange={(o) => !o && setConfirm(null)}
        title="Publish to the official table?" confirmLabel={`Publish ${ready}`} pending={publish.isPending}
        description={`Copies ${ready} approved record(s) from staging to the official clients table.`}
        onConfirm={() => allowedNow("review") && publish.mutate()} />
    </header>
  );
}

function JobControls({ status, control, busy, resuming, onPause, onContinue, onStop, onResume }: {
  status: Status["job_status"]; control: Status["job_control"]; busy: boolean; resuming: boolean;
  onPause: () => void; onContinue: () => void; onStop: () => void; onResume: () => void;
}) {
  const stopping = control === "stop";
  if (status === "running" || status === "paused") {
    const paused = status === "paused" || control === "pause";
    return (
      <div className="flex gap-1">
        {paused
          ? <Button size="sm" variant="outline" disabled={busy || stopping || (status === "paused" && !control)} onClick={onContinue}>
              <Play className="h-4 w-4 shrink-0" /> Continue
            </Button>
          : <Button size="sm" variant="outline" disabled={busy || stopping} onClick={onPause}>
              <Pause className="h-4 w-4 shrink-0" /> Pause
            </Button>}
        <Button size="sm" variant="destructive" disabled={busy || stopping} onClick={onStop}>
          <Square className="h-4 w-4 shrink-0" /> {stopping ? "Stopping…" : "Stop"}
        </Button>
      </div>
    );
  }
  if (status === "interrupted" || status === "stopped" || status === "failed") {
    return (
      <Button size="sm" disabled={busy} onClick={onResume}>
        <Play className="h-4 w-4 shrink-0" /> {resuming ? "Resuming…" : "Resume"}
      </Button>
    );
  }
  return null;
}
