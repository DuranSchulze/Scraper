"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Pause, Play, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { keys, post, statusQuery } from "@/lib/api";
import { list, stamp } from "@/lib/format";
import type { Status } from "@/lib/types";
import { Pill } from "./badges";
import { ConfirmDialog } from "./confirm-dialog";
import { RulesDialog } from "./text-dialog";

const SOURCES = [["gmail", "Gmail"], ["contacts", "Contacts"], ["sheets", "Sheets"]] as const;

const JOB_STATE = {
  running: { label: "Running", tone: "info" },
  paused: { label: "Paused", tone: "warning" },
  stopped: { label: "Stopped", tone: "neutral" },
  done: { label: "Finished", tone: "success" },
  done_with_errors: { label: "Finished with errors", tone: "warning" },
  failed: { label: "Failed", tone: "danger" },
  interrupted: { label: "Interrupted", tone: "warning" },
} as const;

export function JobPanel() {
  const qc = useQueryClient();
  const { data: s, error } = useQuery(statusQuery);
  const [sources, setSources] = useState<string[]>(["gmail", "contacts", "sheets"]);
  const [mailboxes, setMailboxes] = useState("");
  const [since, setSince] = useState("");
  const [full, setFull] = useState(false);
  const [confirm, setConfirm] = useState<null | "extract" | "publish" | "stop">(null);
  const [rules, setRules] = useState(false);
  const logRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [s?.log.length]);

  // Records appear while a job runs; refresh the list on each poll and once more when it ends.
  const running = !!s?.running;
  useEffect(() => { qc.invalidateQueries({ queryKey: ["candidates"] }); }, [running, s?.log.length, qc]);

  const started = () => { qc.invalidateQueries({ queryKey: keys.status }); };
  const fail = (e: Error) => toast.error(e.message);
  const scrape = useMutation({
    mutationFn: () => post("/api/scrape", { sources, mailboxes: list(mailboxes), since, full }),
    onSuccess: () => { toast.success("Scrape started"); started(); }, onError: fail,
  });
  const extract = useMutation({
    mutationFn: () => post("/api/extract", { full: true }),
    onSuccess: () => { setConfirm(null); toast.success("Extraction started"); started(); }, onError: fail,
  });
  const resume = useMutation({
    mutationFn: (id: number) => post(`/api/jobs/${id}/resume`),
    onSuccess: () => { toast.success("Resumed"); started(); }, onError: fail,
  });
  // Pause/continue/stop take effect at the next safe point (after the current batch is saved).
  const control = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "pause" | "continue" | "stop" }) => post(`/api/jobs/${id}/${action}`),
    onSuccess: (_r, v) => { if (v.action === "stop") setConfirm(null); started(); },
    onError: (e: Error) => { fail(e); started(); },
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

  const c = s?.candidates ?? {}, raw = s?.raw_items ?? {};
  const state = s?.job_status ? JOB_STATE[s.job_status] : null;
  const ready = s?.ready_to_publish ?? 0;
  const jobId = s?.job_id ?? null;
  const pendingNote = s?.job_control === "stop" ? "Stopping after the current batch is saved…"
    : s?.job_control === "pause" && s.job_status === "running" ? "Pausing after the current batch is saved…"
    : s?.job_status === "paused" && !s.job_control ? "Continuing…" : null;

  return (
    <header className="flex flex-col gap-4 border-b bg-card p-4 lg:p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">Workspace Scraper</h1>
        <p className="text-xs text-muted-foreground tabular-nums">
          Raw: gmail {raw.gmail ?? 0}, contacts {raw.contacts ?? 0}, sheets {raw.sheets ?? 0} · Pending {c.pending ?? 0} ·
          Approved {c.approved ?? 0} · Rejected {c.rejected ?? 0} · Merged {c.merged ?? 0}
        </p>
      </div>

      {error && (
        <div role="alert" className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error.message} Retrying every few seconds…
        </div>
      )}
      {!!s?.extract_failed && (
        <p className="text-sm font-medium text-warning">{s.extract_failed} item(s) failed extraction and will be retried on the next run.</p>
      )}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,22rem)_minmax(0,1fr)_minmax(0,14rem)]">
        <fieldset className="flex min-w-0 flex-col gap-2 rounded-xl border p-3">
          <legend className="px-1 text-sm font-medium text-muted-foreground">Scrape</legend>
          <div className="flex flex-wrap gap-4">
            {SOURCES.map(([v, label]) => (
              <Label key={v} className="gap-2 font-normal">
                <Checkbox checked={sources.includes(v)}
                  onCheckedChange={(on) => setSources((x) => on ? [...x, v] : x.filter((y) => y !== v))} />
                {label}
              </Label>
            ))}
          </div>
          <Label htmlFor="mailboxes" className="text-xs text-muted-foreground">Mailboxes (comma list, blank = all users)</Label>
          <Input id="mailboxes" value={mailboxes} onChange={(e) => setMailboxes(e.target.value)} placeholder="sales@company.com, jane@company.com" />
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex flex-col gap-1">
              <Label htmlFor="since" className="text-xs text-muted-foreground">Since</Label>
              <Input id="since" type="date" value={since} onChange={(e) => setSince(e.target.value)} className="w-40" />
            </div>
            <Label className="mb-2 gap-2 font-normal"><Checkbox checked={full} onCheckedChange={(v) => setFull(!!v)} /> Full rescan</Label>
          </div>
          <div className="flex flex-wrap gap-2 pt-1">
            <Button disabled={running || scrape.isPending || !sources.length} onClick={() => scrape.mutate()}>
              {scrape.isPending ? "Starting…" : "Run scrape"}
            </Button>
            <Button variant="outline" disabled={running} onClick={() => setConfirm("extract")}>Re-run extraction</Button>
            <Button variant="ghost" onClick={() => setRules(true)}>View rules</Button>
          </div>
        </fieldset>

        <section className="flex min-w-0 flex-col gap-2" aria-labelledby="logTitle">
          <div className="flex flex-wrap items-center gap-2">
            <h2 id="logTitle" className="text-sm font-medium text-muted-foreground">
              {s?.job ? `Last job: ${s.job}` : "Log"}
            </h2>
            {state && <Pill tone={state.tone}>{state.label}{s?.job_failures ? ` · ${s.job_failures} failure(s)` : ""}</Pill>}
            {s?.job_started_at && <span className="text-xs text-muted-foreground">started {stamp(s.job_started_at)}</span>}
            {jobId && <JobControls status={s!.job_status} control={s!.job_control} busy={control.isPending || resume.isPending}
              onPause={() => control.mutate({ id: jobId, action: "pause" })}
              onContinue={() => control.mutate({ id: jobId, action: "continue" })}
              onStop={() => setConfirm("stop")}
              onResume={() => resume.mutate(jobId)} resuming={resume.isPending} />}
          </div>
          {pendingNote && <p className="text-xs text-muted-foreground" role="status">{pendingNote}</p>}
          {s?.job_status === "interrupted" && (
            <p className="text-xs text-muted-foreground">The server stopped while this job ran. Resume continues it; already fetched mail is skipped.</p>
          )}
          {s?.job_status === "stopped" && (
            <p className="text-xs text-muted-foreground">Stopped. Everything saved so far is kept; Resume continues from here.</p>
          )}
          {s?.error && s.job_status === "failed" && <p className="text-xs break-words text-destructive">{s.error}</p>}
          <pre ref={logRef} aria-live="polite" tabIndex={0} aria-label="Job log"
            className="h-[260px] w-full max-w-[680px] min-w-0 resize overflow-auto rounded-lg bg-foreground p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap text-background">
            {s?.log.length ? s.log.join("\n") : "No job has run yet."}
          </pre>
        </section>

        <section className="flex min-w-0 flex-col gap-2" aria-labelledby="pubTitle">
          <h2 id="pubTitle" className="text-sm font-medium text-muted-foreground">Official table</h2>
          <p className="text-xs text-muted-foreground">Approved records stay in staging until published.</p>
          <Button disabled={!ready || publish.isPending} onClick={() => setConfirm("publish")}>
            Publish {ready} approved
          </Button>
        </section>
      </div>

      <ConfirmDialog open={confirm === "extract"} onOpenChange={(o) => !o && setConfirm(null)}
        title="Re-run extraction?" confirmLabel="Re-run extraction" pending={extract.isPending}
        description="Re-reads every stored item with the current rules. Pending records are rebuilt; approved and rejected ones are kept. Nothing is downloaded from Google."
        onConfirm={() => extract.mutate()} />
      <ConfirmDialog open={confirm === "publish"} onOpenChange={(o) => !o && setConfirm(null)}
        title="Publish to the official table?" confirmLabel={`Publish ${ready}`} pending={publish.isPending}
        description={`Copies ${ready} approved record(s) from staging to the official clients table.`}
        onConfirm={() => publish.mutate()} />
      <ConfirmDialog open={confirm === "stop"} onOpenChange={(o) => !o && setConfirm(null)}
        title={`Stop the ${s?.job || "job"}?`} confirmLabel="Stop" pending={control.isPending}
        description="It stops after the current batch is saved. Everything saved so far is kept, and Resume continues from where it stopped."
        onConfirm={() => jobId && control.mutate({ id: jobId, action: "stop" })} />
      {rules && <RulesDialog onClose={() => setRules(false)} />}
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
  if (status === "interrupted" || status === "stopped") {
    return (
      <Button size="sm" disabled={busy} onClick={onResume}>
        <Play className="h-4 w-4 shrink-0" /> {resuming ? "Resuming…" : "Resume"}
      </Button>
    );
  }
  return null;
}
