"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pause, Play, Square, Timer, Upload } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import { boardQuery, canUse, keys, meQuery, post, scheduleQuery, statusQuery } from "@/lib/api";
import { stamp } from "@/lib/format";
import type { JobProgress, RouteKey, Status, User } from "@/lib/types";
import { clock, stageView } from "@/lib/progress";
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

        {canScrape && (running
          ? <StageProgress progress={s?.job_progress ?? null} paused={s?.job_status === "paused"} running={board?.counts?.running ?? 0} />
          : <NextScrape />)}

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

/** Current time, re-read every second while mounted. */
function useNow() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  return now;
}

/** The running job's stage with its own bar and count, plus time spent in that stage. Never a full bar while work remains. */
function StageProgress({ progress, paused, running }: { progress: JobProgress | null; paused: boolean; running: number }) {
  const now = useNow();
  const view = progress ? stageView(progress) : { label: "Starting", count: "", value: null };
  const elapsed = progress ? clock(now - new Date(progress.started_at).getTime()) : "";
  const label = `${view.label}${paused ? " (paused)" : ""}`;
  return (
    <div className="flex min-w-[14rem] flex-1 flex-col gap-1 sm:max-w-sm">
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="truncate font-medium text-foreground">{label}</span>
        <span className="whitespace-nowrap text-muted-foreground tabular-nums">
          {view.count}{view.count && progress?.stage === "fetch" && running ? ` · ${running} running` : ""}{elapsed ? `${view.count ? " · " : ""}${elapsed}` : ""}
        </span>
      </div>
      {view.value == null
        ? <div role="progressbar" aria-label={label} aria-valuetext={`${label}, total not known yet`} className="relative h-1 w-full overflow-hidden rounded-full bg-muted">
            <div className={cn("absolute inset-y-0 w-1/3 rounded-full bg-primary/70", !paused && "motion-safe:animate-[stage-sweep_1.4s_ease-in-out_infinite]")} />
          </div>
        : <Progress value={view.value} aria-label={label} aria-valuetext={`${label}: ${view.count}`} />}
    </div>
  );
}

/** Idle: a live countdown to the next automatic scrape, from the saved schedule. */
function NextScrape() {
  const now = useNow();
  const { data: sched } = useQuery(scheduleQuery);
  if (!sched) return null;
  const at = sched.next_run_at ? new Date(sched.next_run_at).getTime() : null;
  const text = !sched.enabled ? "Auto-scrape off"
    : at == null ? "Next auto-scrape after the current job"
    : at - now <= 0 ? "Auto-scrape starting…"
    : `Next auto-scrape in ${clock(at - now)}`;
  return (
    <span role="timer" aria-live="off" className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-xs text-muted-foreground tabular-nums"
      title={at ? new Date(at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : undefined}>
      <Timer aria-hidden className="h-3.5 w-3.5 shrink-0" />{text}
    </span>
  );
}
